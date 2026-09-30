"""MathForge 模块 G：薄弱模式卡自动蒸馏器（2026-09-29）。

借鉴 CC services/SessionMemory 的「后台自动笔记」模式：学生练满一定量后，
无需用户触发，把该 (pack, kp) 的作答史 + 归因总结自动蒸馏成一张薄弱模式卡
写入 patterns 表（source="auto_distill"，confirmed=False）。P1-P6 决策层与
出题链不变；模块 F 的 recall_for_generation → format_memory_block 已消费
pattern_card，蒸馏出的卡自然进入出题记忆块（本模块不做出题侧改动）。

红线（全部落为代码行为）：
- demo 态整体跳过：MATHFORGE_DEMO=1 或无 DEEPSEEK_API_KEY（口径同
  v2/router._demo_mode / v2api._llm_available）→ 直接 {"distilled": False,
  "reason": "demo"}，不查库、不调 LLM、不报错——后台增强绝不允许打断学生练习。
- 判分权不在 LLM：模式卡只是学习建议文本，绝不参与判分/决策/调度逻辑。
- sim 隔离：只认传入 conn，绝不自连任何库（与 mem2 模块头同口径）；模拟器
  调用方传入指向 mathforge_sim.db 的连接，数据天然不混默认库。
- 绝不抛出：调用方在请求路径上，任何异常都折叠为 {"distilled": False, ...}。

触发 / 节流定案：
- 窗口口径与 mem2.recall_for_generation 逐字一致：该 kp 最近 RECALL_WINDOW(=8)
  条有效作答（result ∈ correct/wrong/partial，排除 mode ∈ override/retry）。
- 触发：窗口有效事件 ≥ DISTILL_MIN_EVENTS(6) 且 错题（_is_mistake_row 口径，
  与 project_mistakes 一致）≥ DISTILL_MIN_WRONG(2)。
- 节流：已有 source=auto_distill 的卡 → 卡 created_ts 之后新增有效作答事件 ≥
  DISTILL_REFRESH_EVENTS(3) 才重蒸（不是每答一题都蒸馏）；既有卡是 rule 卡
  （/v2/patterns 物化，每次调用都刷新 created_ts）则视为「无自动卡」，达阈值
  即升级覆盖——否则规则卡会把节流时钟永远重置，自动卡被饿死（对抗审查 major）。
- 并发去重：同一 (pack, kp) 蒸馏在途时直接 in_progress 返回（对抗审查 minor）。
- LLM 契约：namespace="distill:"，temperature=0.2，输出 JSON {"pattern_md": ...}
  （3-6 句中文：反复错误模式、典型错因、练习建议）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading

import mem2
from llm import chat_json

# 触发阈值：窗口有效事件 ≥6 且错题 ≥2 才值得蒸馏（样本太少是噪声）。
DISTILL_MIN_EVENTS = 6
DISTILL_MIN_WRONG = 2
# 节流：卡创建后新增有效事件 ≥3 才重蒸（窗口口径统计）。
DISTILL_REFRESH_EVENTS = 3

# pattern_md 落库上限（LLM 契约 3-6 句，防御性截断；出题记忆块注入侧另有 160 字截取）
_PATTERN_MD_MAX = 1000

# per-(pack, kp) 在途去重（对抗审查 minor）：并发双作答同时达阈值（或后台任务与
# 下一次作答的预判重叠）时只有第一个调 LLM，其余直接 in_progress 返回，不浪费调用；
# maybe_distill 无论成功失败都在 finally 清理（见 _INFLIGHT_LOCK 保护段）。
_INFLIGHT: set = set()
_INFLIGHT_LOCK = threading.Lock()

_DISTILL_SYSTEM = (
    "你是考研数学学情分析师。输入是同一名学生在同一考点的最近作答摘要"
    "（对错序列、错因统计、错题摘录）。请蒸馏一张「薄弱模式卡」：3-6 句中文，"
    "说清①该生在此考点的反复错误模式（与对错序列一致）、②典型错因"
    "（只依据错因统计与摘录，不臆造）、③下一步练习建议（具体可执行）。"
    "语气面向学生；不判分、不给成绩评价。只输出 JSON：{\"pattern_md\": \"...\"}"
)


# --------------------------------------------------------------------------- #
# demo 判定（口径同 v2/router._demo_mode）
# --------------------------------------------------------------------------- #
def _demo_mode() -> bool:
    """demo（零 API）态：MATHFORGE_DEMO=1 或无 DEEPSEEK_API_KEY。

    与 v2/router._demo_mode、v2api._llm_available 同一口径；刻意不复用 import
    （v2api↔router 依赖包加载重，蒸馏器保持零依赖轻量，口径以注释锚定）。
    """
    if os.environ.get("MATHFORGE_DEMO") == "1":
        return True
    return not os.environ.get("DEEPSEEK_API_KEY")


# --------------------------------------------------------------------------- #
# 窗口取数（口径 = mem2.recall_for_generation / project_mastery）
# --------------------------------------------------------------------------- #
def _window_rows(conn: sqlite3.Connection, kp: str) -> list:
    """该 kp 最近 RECALL_WINDOW 条有效作答事件（新→旧）。

    result IN (correct,wrong,partial) 且 mode NOT IN (override,retry)——与
    mem2.recall_for_generation 的 WHERE 逐字一致；不按 sim 过滤（隔离靠 conn）。
    """
    return conn.execute(
        "SELECT * FROM attempt_events "
        "WHERE kp=? AND result IN ('correct','wrong','partial') "
        "AND mode NOT IN ('override','retry') "
        "ORDER BY ts DESC, id DESC LIMIT ?",
        (kp, mem2.RECALL_WINDOW),
    ).fetchall()


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# 状态 / 触发预判（纯 SQL，零 LLM）
# --------------------------------------------------------------------------- #
def distill_status(conn: sqlite3.Connection, pack: str, kp: str) -> dict:
    """只读诊断 + 触发预判（纯 SQL，零 LLM）。

    v2api 请求路径用它做廉价预判：due=False 直接跳过，常态每次作答只有两条
    COUNT 级查询的开销；due=True 才值得挂后台蒸馏任务。

    返回：
      {pack, kp, n, wrong,                    # 窗口有效事件数 / 错题数
       has_card, card_source, card_confirmed, # 当前模式卡（patterns_get，任意来源）
       has_auto_card,                         # 是否存在 source=auto_distill 的卡（节流相关）
       events_after_card,                     # auto 卡 created_ts 后新增有效事件数（无 auto 卡 None）
       threshold_met,                         # n/wrong 达触发阈值
       expired,                               # auto 卡已过期（≥3 条新事件）
       due}                                   # threshold_met 且（无 auto 卡或已过期）
    """
    rows = _window_rows(conn, kp)
    n = len(rows)
    wrong = sum(1 for r in rows if mem2._is_mistake_row(r["mode"], r["result"]))

    card = None
    try:
        card = mem2.patterns_get(conn, pack, kp)
    except sqlite3.Error:  # 表异常按无卡处理（预判失败由调用方兜底跳过）
        card = None

    # 对抗审查修复（major）：节流只对自动蒸馏卡生效——既有卡是 rule 卡
    # （/v2/patterns 每次物化都 upsert 刷新 created_ts）时视为「无自动卡」，
    # 达阈值即升级覆盖。否则学生每答 2-3 题逛一次学情页，rule 卡的 created_ts
    # 永远新鲜、events_after_card 永远 <3，自动蒸馏被永久饿死。
    auto_card = card is not None and card.get("source") == "auto_distill"

    events_after = None
    if auto_card:
        created = _to_float(card.get("created_ts"))
        row = conn.execute(
            "SELECT COUNT(*) FROM attempt_events "
            "WHERE kp=? AND ts>? AND result IN ('correct','wrong','partial') "
            "AND mode NOT IN ('override','retry')",
            (kp, created),
        ).fetchone()
        events_after = int(row[0] or 0)

    threshold_met = n >= DISTILL_MIN_EVENTS and wrong >= DISTILL_MIN_WRONG
    expired = auto_card and events_after is not None \
        and events_after >= DISTILL_REFRESH_EVENTS
    return {
        "pack": pack,
        "kp": kp,
        "n": n,
        "wrong": wrong,
        "has_card": card is not None,
        "card_source": card.get("source") if card else None,
        "card_confirmed": bool(card.get("confirmed")) if card else None,
        "has_auto_card": auto_card,
        "events_after_card": events_after,
        "threshold_met": threshold_met,
        "expired": expired,
        "due": threshold_met and (not auto_card or expired),
    }


# --------------------------------------------------------------------------- #
# 蒸馏输入（payload）—— 归因/摘录口径复用 mem2（override 优先、占位串同 None）
# --------------------------------------------------------------------------- #
def _build_payload(conn: sqlite3.Connection, pack: str, kp: str) -> dict:
    """窗口事件摘要：结果序列 + 归因统计 + 错题摘录。

    口径与 mem2.recall_for_generation 一致：
    - 归因 = _resolve_attribution（独立 override 事件 > 事件自身 user_override >
      attribution）；占位串「未归因」（attribute.py 失败兜底）与 None 同义，
      不进 attribution_counts；摘录行标注「未归因」。
    - 摘录含 meta.q/ans（F1 落库键），旧事件无此键 → 键缺省省略，绝不报错。
    """
    rows = _window_rows(conn, kp)
    override_map = mem2._latest_overrides(conn, None, kp)
    counts: dict = {}
    excerpts: list[dict] = []
    for r in rows:  # rows 新→旧；摘录新→旧
        if not mem2._is_mistake_row(r["mode"], r["result"]):
            continue
        attr = mem2._resolve_attribution(r, override_map.get(r["id"]))
        attr_type = mem2._real_attr_type(attr)
        if attr_type:
            counts[attr_type] = counts.get(attr_type, 0) + 1
        entry: dict = {"day": r["day"], "attribution_type": attr_type or "未归因"}
        meta = mem2._parse_json(r["meta"])
        if isinstance(meta, dict):
            if meta.get("q"):
                entry["q"] = str(meta["q"])
            if meta.get("ans"):
                entry["ans"] = str(meta["ans"])
        excerpts.append(entry)
    return {
        "pack": pack,
        "kp": kp,
        "window_n": len(rows),
        "wrong_n": len(excerpts),
        "result_seq": [r["result"] for r in reversed(rows)],  # 时间正序（旧→新）
        "attribution_counts": counts,          # 仅真实错因计数（占位/None 不计）
        "wrong_excerpts": excerpts,            # 新→旧
    }


def _messages(payload: dict) -> list[dict]:
    user = ("以下是该学生在同一考点的最近作答摘要（JSON）：\n"
            + json.dumps(payload, ensure_ascii=False, indent=1)
            + "\n请据此蒸馏一张薄弱模式卡。")
    return [{"role": "system", "content": _DISTILL_SYSTEM},
            {"role": "user", "content": user}]


def _brief(e: BaseException, limit: int = 200) -> str:
    """异常单行摘要（截断），拼进返回值供诊断日志，不裸抛。"""
    s = " ".join(f"{type(e).__name__}: {e}".split())
    return s if len(s) <= limit else s[:limit] + "…"


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def maybe_distill(conn: sqlite3.Connection, pack: str, kp: str, *,
                  llm=None) -> dict:
    """按需蒸馏一张薄弱模式卡（绝不抛出；demo 态整体跳过）。

    llm：chat_json 兼容假件注入点（测试用）。None → 调用期解析模块级
    chat_json（与签名默认 llm=chat_json 同义；刻意不做 def 期默认参数绑定，
    HTTP 集成测试可 monkeypatch distill.chat_json 换假件，真实路径零感知）。

    并发去重：同一 (pack, kp) 已有蒸馏在途 → 直接
    {"distilled": False, "reason": "in_progress"}（零 LLM），无论成败都在
    finally 清理在途标记，绝不卡死后续蒸馏。

    返回：
      {"distilled": True, "pattern_md", "source": "auto_distill",
       "confirmed": False, "status": <distill_status>}
      或 {"distilled": False, "reason": demo|in_progress|threshold|throttled|
          llm_error|error, ["error": 异常摘要], ["status": 预判结果]}
    """
    # 1) demo 红线：先于任何库查询与 LLM——零成本整体跳过，不打断学生练习。
    if _demo_mode():
        return {"distilled": False, "reason": "demo"}
    # 2) 在途去重（per-(pack,kp)）：并发双作答同时达阈值只蒸一次
    key = (str(pack), str(kp))
    with _INFLIGHT_LOCK:
        if key in _INFLIGHT:
            return {"distilled": False, "reason": "in_progress"}
        _INFLIGHT.add(key)
    try:
        return _distill_locked(conn, pack, kp, llm)
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.discard(key)


def _distill_locked(conn: sqlite3.Connection, pack: str, kp: str, llm) -> dict:
    """maybe_distill 主体（调用方已持有在途标记；任何异常折叠为返回值）。"""
    # 3) 触发/节流预判（纯 SQL）
    try:
        st = distill_status(conn, pack, kp)
    except Exception as e:  # noqa: BLE001
        return {"distilled": False, "reason": "error", "error": _brief(e)}
    if not st["due"]:
        return {"distilled": False,
                "reason": "threshold" if not st["threshold_met"] else "throttled",
                "status": st}

    # 4) 蒸馏输入
    try:
        messages = _messages(_build_payload(conn, pack, kp))
    except Exception as e:  # noqa: BLE001
        return {"distilled": False, "reason": "error", "error": _brief(e)}

    # 5) LLM 蒸馏：解析失败/异常 → llm_error，绝不向调用方抛出
    try:
        chat = llm if llm is not None else chat_json
        d = chat(messages, temperature=0.2, namespace="distill:")
        pattern_md = ""
        if isinstance(d, dict):
            pattern_md = str(d.get("pattern_md") or "").strip()
        if not pattern_md:
            raise ValueError(f"LLM 输出缺少 pattern_md：{str(d)[:120]}")
    except Exception as e:  # noqa: BLE001
        return {"distilled": False, "reason": "llm_error", "error": _brief(e)}

    # 6) 写卡（学习建议，非判分；confirmed=False 待人工/后续确认流）
    try:
        mem2.patterns_set(conn, pack, kp, pattern_md[:_PATTERN_MD_MAX],
                          source="auto_distill", confirmed=False)
    except Exception as e:  # noqa: BLE001
        return {"distilled": False, "reason": "error", "error": _brief(e)}
    return {"distilled": True, "pattern_md": pattern_md[:_PATTERN_MD_MAX],
            "source": "auto_distill", "confirmed": False, "status": st}
