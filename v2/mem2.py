"""MathForge 记忆层 v2 —— attempt_events 事件流 + 三投影（D024/D025/D026 定案）。

设计要点：
- attempt_events 为 append-only 唯一事实源；mastery / mistakes / patterns 为离线可重算的投影。
- 修复 v1 N=1 缺口：掌握度改用最近 N=5 滑窗加权（权重 0.5^(age_days/halflife)）。
- 本模块只读 v1 文件（db.py/policy.py/app.py 等），绝不修改；与 v1 共用同一 SQLite 文件但只新增表。

⚠️ sim 隔离（红线）：
  模拟器数据必须走独立 conn 指向独立库 mathforge_sim.db，绝不混入默认库 mathforge.db。
  本模块所有函数都显式接受 conn 参数，调用方负责传入正确的连接；mem2 自身不决定用哪个库。
  默认库（default_db_path）仅用于人类真实作答数据。
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# 幂等迁移机制（模块 C，2026-09-28）：v2 两表 DDL 已原样迁入迁移 0001_v2_tables。
# 优先平铺导入（v2/ 在 sys.path，app.py / conftest / scripts 均如此）；
# 以包形态导入 v2.mem2 时回落到相对导入。
try:
    from migrations import ensure_migrated
except ImportError:  # pragma: no cover - 包形态导入兜底
    from .migrations import ensure_migrated

# 与 v1 db.py 同一定义：mathforge.db 位于仓库根目录（mem2.py 同目录）。
# v1 db.py: DB_PATH = Path(__file__).resolve().parents[1] / "mathforge.db"
DB_PATH = Path(__file__).resolve().parents[1] / "mathforge.db"

# 默认半衰期（与 v1 db.HALF_LIFE_DAYS、pack_loader.DEFAULT_STRATEGY 对齐）。
DECAY_HALF_LIFE_DAYS = 7.0
DEFAULT_WINDOW = 5
DEFAULT_RECENCY_HALFLIFE_DAYS = 7.0

# 评分映射
SCORE = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}

_SCORE_RESULTS = ("correct", "wrong", "partial")


# --------------------------------------------------------------------------- #
# 连接 / schema
# --------------------------------------------------------------------------- #
def default_db_path() -> Path:
    """返回与 v1 db.py 同一默认库文件（仓库根目录 mathforge.db）。

    真实作答数据落此库；模拟器须自建 mathforge_sim.db 并传独立 conn。
    """
    return DB_PATH


def init_schema(conn: sqlite3.Connection) -> None:
    """在同库新增 attempt_events 与 patterns 两表（不动 v1 三表）。幂等（IF NOT EXISTS）。

    2026-09-28 起委托 v2/migrations.ensure_migrated：两表 + 两索引的 DDL 原样迁入
    迁移 0001_v2_tables（IF NOT EXISTS 逐字未改），并登记 schema_migrations。
    对外签名与行为不变：建好表、commit、幂等；对「已建好全部表的现有库」首次
    执行零 DDL 副作用，仅正常登记。
    """
    ensure_migrated(conn)


def connect(db_path=None) -> sqlite3.Connection:
    """便捷：打开连接 + 初始化 v2 schema（不影响 v1 三表）。"""
    path = str(db_path) if db_path is not None else str(default_db_path())
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


# --------------------------------------------------------------------------- #
# 策略参数解析（兼容 dict 形态 DEFAULT_STRATEGY 或属性对象）
# --------------------------------------------------------------------------- #
def _window(strategy) -> int:
    if not strategy:
        return DEFAULT_WINDOW
    m = _deep_get(strategy, ("mastery", "window"))
    return int(m) if m is not None else DEFAULT_WINDOW


def _recency_halflife(strategy) -> float:
    if not strategy:
        return DEFAULT_RECENCY_HALFLIFE_DAYS
    v = _deep_get(strategy, ("mastery", "recency_halflife_days"))
    return float(v) if v is not None else DEFAULT_RECENCY_HALFLIFE_DAYS


def _decay_halflife(strategy) -> float:
    if not strategy:
        return DECAY_HALF_LIFE_DAYS
    v = _deep_get(strategy, ("decay_half_life_days",))
    return float(v) if v is not None else DECAY_HALF_LIFE_DAYS


def _deep_get(obj, keys):
    cur = obj
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            cur = getattr(cur, k, None)
        if cur is None:
            return None
    return cur


# --------------------------------------------------------------------------- #
# 衰减
# --------------------------------------------------------------------------- #
def _decayed(value: float, updated_at: float, at: float, half_life: float) -> float:
    """对齐 v1 db.decayed：value * 0.5 ** (经过天数 / half_life)，天数下限为 0。"""
    days = max(0.0, (at - updated_at) / 86400.0)
    return value * math.pow(0.5, days / half_life)


# --------------------------------------------------------------------------- #
# 写入：append + override
# --------------------------------------------------------------------------- #
def append_event(
    conn: sqlite3.Connection,
    *,
    pack: str,
    kp: str,
    qtype: str,
    mode: str,
    result: str,
    attribution=None,
    user_override=None,
    policy_snapshot=None,
    meta=None,
    ts=None,
    day=None,
    sim: int = 0,
) -> int:
    """追加一条作答/评估事件到 attempt_events（append-only 事实源）。

    attribution 形如 {"type": "概念混淆", "conf": 0.9}。
    result ∈ {correct, wrong, partial}。
    sim=1 表示模拟器事件——调用方必须已把 conn 指向 mathforge_sim.db，切勿混入默认库。

    返回新事件 id。
    """
    if ts is None:
        ts = time.time()
    if day is None:
        # 真实时间戳 → 本地日历日（"今日/打卡/热力图"以用户本地日为准，修正 UTC 跨日错位）；
        # 合成/测试用 <1970 旧时间戳 → timedelta UTC 推算（规避 Windows localtime 截断）。
        if ts >= 10**9:
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        else:
            day = (date(1970, 1, 1) + timedelta(seconds=ts)).strftime("%Y-%m-%d")

    cur = conn.execute(
        """
        INSERT INTO attempt_events
            (ts, day, sim, pack, kp, qtype, mode, result,
             attribution, user_override, policy_snapshot, meta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            day,
            int(sim),
            pack,
            kp,
            qtype,
            mode,
            result,
            _json_or_none(attribution),
            _json_or_none(user_override),
            _json_or_none(policy_snapshot),
            _json_or_none(meta),
        ),
    )
    conn.commit()
    return cur.lastrowid


def override_attribution(
    conn: sqlite3.Connection,
    origin_event_id: int,
    new_type: str,
    conf: float = 1.0,
) -> int:
    """人类修正写回事件流：追加一条 mode='override' 事件。

    meta.origin_event_id = origin_event_id；user_override = {"type": new_type, "conf": conf}。
    投影时按 origin_event_id 取最近 override 覆盖原归因（user_override 优先）。
    """
    return append_event(
        conn,
        pack="",  # 修正事件不绑定具体 pack/kp 语义，靠 meta.origin_event_id 关联
        kp="",
        qtype="",
        mode="override",
        result="override",
        user_override={"type": new_type, "conf": conf},
        meta={"origin_event_id": origin_event_id},
    )


# --------------------------------------------------------------------------- #
# 投影：掌握度
# --------------------------------------------------------------------------- #
def project_mastery(conn, kp: str, at=None, strategy=None) -> dict | None:
    """滑窗 N=window(默认5) 加权掌握度；取该 kp 最近 N 条 result∈{correct,wrong,partial}
    （排除 mode='override'）。

    返回 {value, n, last_ts, decayed_value}；无事件返回 None。
    value = Σ w_i·s_i / Σ w_i；w_i = 0.5^(age_days_i / recency_halflife_days)。
    decayed_value = 对齐 v1 半衰期语义 decayed(value, last_ts, at)。
    """
    if at is None:
        at = time.time()
    window = _window(strategy)
    recency_hl = _recency_halflife(strategy)
    decay_hl = _decay_halflife(strategy)

    rows = conn.execute(
        """
        SELECT ts, result FROM attempt_events
        WHERE kp=? AND result IN ('correct','wrong','partial')
          AND mode NOT IN ('override','retry')
        ORDER BY ts DESC, id DESC
        LIMIT ?
        """,
        (kp, window),
    ).fetchall()

    n = len(rows)
    if n == 0:
        return None

    num = 0.0
    den = 0.0
    last_ts = None
    for r in rows:
        age_days = (at - r["ts"]) / 86400.0
        w = math.pow(0.5, age_days / recency_hl)
        s = SCORE.get(r["result"], 0.0)
        num += w * s
        den += w
        if last_ts is None or r["ts"] > last_ts:
            last_ts = r["ts"]

    value = num / den if den > 0 else 0.0
    if n == 1:
        # 首答封顶（学生复评：一道基础题就把掌握度刷到 100% 是虚假安全感）
        value = min(value, 0.7)
    decayed_value = _decayed(value, last_ts, at, decay_hl)
    return {
        "value": value,
        "n": n,
        "last_ts": last_ts,
        "decayed_value": decayed_value,
    }


# --------------------------------------------------------------------------- #
# 投影：错题 + 归因（含 override 优先）
# --------------------------------------------------------------------------- #
def project_mistakes(conn, pack=None, kp=None, limit: int = 20) -> list[dict]:
    """错题事件（result='wrong' 或 mode='selfassess' 且 result='partial'），
    每条带最新归因（user_override 优先：取该源事件之后最近的 override 事件）。

    返回 list[dict]，按 ts 倒序（最近在前）。
    """
    clauses = ["(result='wrong' OR (mode='selfassess' AND result='partial'))"]
    params: list = []
    if pack is not None:
        clauses.append("pack=?")
        params.append(pack)
    if kp is not None:
        clauses.append("kp=?")
        params.append(kp)
    where = " AND ".join(clauses)

    rows = conn.execute(
        f"SELECT * FROM attempt_events WHERE {where} ORDER BY ts DESC, id DESC LIMIT ?",
        params + [limit],
    ).fetchall()

    # 预取该范围下的 override 事件映射 origin_event_id -> 最新 user_override
    override_map = _latest_overrides(conn, pack, kp)

    out = []
    for r in rows:
        rid = r["id"]
        attr = _resolve_attribution(r, override_map.get(rid))
        out.append(
            {
                "id": rid,
                "ts": r["ts"],
                "day": r["day"],
                "pack": r["pack"],
                "kp": r["kp"],
                "qtype": r["qtype"],
                "mode": r["mode"],
                "result": r["result"],
                "attribution": attr,
            }
        )
    return out


def _latest_overrides(conn, pack, kp) -> dict:
    """返回 {origin_event_id: user_override_dict}，取每个源事件之后最近的 override。"""
    clauses = ["mode='override'"]
    params: list = []
    if pack is not None:
        # pack 过滤仅在 override 事件也记录了对应 pack 时有意义；此处宽松处理
        pass
    rows = conn.execute(
        "SELECT id, ts, meta, user_override FROM attempt_events WHERE mode='override' "
        "ORDER BY ts ASC, id ASC"
    ).fetchall()
    # 按 origin_event_id 保留最新（ts 最大）的 override
    best: dict = {}
    for r in rows:
        meta = _parse_json(r["meta"])
        if not isinstance(meta, dict):
            continue
        oid = meta.get("origin_event_id")
        if oid is None:
            continue
        # 仅当比已记录更新时覆盖（rows 已按 ts 升序，后写的更大）
        best[oid] = _parse_json(r["user_override"])
    return best


def _resolve_attribution(event_row, override_user_override) -> dict:
    """合并：独立 override 事件 > 事件自身 user_override > 事件 attribution。

    注意：user_override 列无类型亲和，调用方可能写入 0 / "0"（表示“无修正”）。
    按 `int(row['user_override'] or 0)` 语义，0/"0"/None/"" 一律视为无修正，
    仅当解析结果为非空（真实修正 dict 或类型字符串）才采用。
    """
    if override_user_override is not None:
        return _override_to_attr(override_user_override)
    own = _parse_json(event_row["user_override"])
    if own:  # 非空：真实修正 dict 或类型字符串；0/"0"/None/"" 视为无修正
        return _override_to_attr(own)
    attr = _parse_json(event_row["attribution"])
    if isinstance(attr, dict):
        return attr
    return {"type": None, "conf": None}


def _override_to_attr(uo) -> dict:
    if isinstance(uo, dict):
        t = uo.get("type")
        c = uo.get("conf", 1.0)
    else:
        t = uo
        c = 1.0
    return {"type": t, "conf": c, "overridden": True}


# --------------------------------------------------------------------------- #
# 投影：全量
# --------------------------------------------------------------------------- #
def project_all(conn, strategy=None) -> dict:
    """全 kp 投影：{kp: {mastery, decayed_value, n, last_ts, streak_correct, last_attribution}}。

    streak_correct = 按 ts 从最近往前连续 correct 次数（N≥1 且最近一条为 correct 才有值）。
    """
    kps = conn.execute(
        "SELECT DISTINCT kp FROM attempt_events "
        "WHERE result IN ('correct','wrong','partial') AND mode<>'override'",
    ).fetchall()
    result = {}
    for (kp,) in kps:
        m = project_mastery(conn, kp, at=None, strategy=strategy)
        if m is None:
            continue
        streak = _streak_correct(conn, kp)
        last_attr = _last_attribution(conn, kp)
        result[kp] = {
            "mastery": m["value"],
            "decayed_value": m["decayed_value"],
            "n": m["n"],
            "last_ts": m["last_ts"],
            "streak_correct": streak,
            "last_attribution": last_attr,
        }
    return result


def _streak_correct(conn, kp: str) -> int:
    rows = conn.execute(
        "SELECT result FROM attempt_events "
        "WHERE kp=? AND result IN ('correct','wrong','partial') "
        "AND mode NOT IN ('override','retry') "
        "ORDER BY ts DESC, id DESC",
        (kp,),
    ).fetchall()
    if not rows:
        return 0
    if rows[0]["result"] != "correct":
        return 0
    streak = 0
    for r in rows:
        if r["result"] == "correct":
            streak += 1
        else:
            break
    return streak


def _last_attribution(conn, kp: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM attempt_events "
        "WHERE kp=? AND result IN ('correct','wrong','partial') AND mode<>'override' "
        "ORDER BY ts DESC, id DESC LIMIT 1",
        (kp,),
    ).fetchone()
    if row is None:
        return None
    override_map = _latest_overrides(conn, None, kp)
    return _resolve_attribution(row, override_map.get(row["id"]))


# --------------------------------------------------------------------------- #
# 薄弱模式卡（v2 蒸馏器 TBD，先建表 + 存取）
# --------------------------------------------------------------------------- #
def patterns_set(conn, pack: str, kp: str, pattern_md: str, source: str,
                 confirmed: bool = False) -> int:
    """写入/更新 (pack, kp) 对应的薄弱模式卡（upsert）。返回 id。"""
    ts = time.time()
    cur = conn.execute(
        """
        INSERT INTO patterns (pack, kp, pattern_md, source, confirmed, created_ts)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(pack, kp) DO UPDATE SET
            pattern_md=excluded.pattern_md,
            source=excluded.source,
            confirmed=excluded.confirmed,
            created_ts=excluded.created_ts
        """,
        (pack, kp, pattern_md, source, int(bool(confirmed)), ts),
    )
    conn.commit()
    return cur.lastrowid


def patterns_get(conn, pack: str, kp: str) -> dict | None:
    """读取 (pack, kp) 对应的薄弱模式卡（最新一条）。无则返回 None。"""
    row = conn.execute(
        "SELECT * FROM patterns WHERE pack=? AND kp=? ORDER BY created_ts DESC, id DESC LIMIT 1",
        (pack, kp),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["confirmed"] = bool(d["confirmed"])
    return d


# --------------------------------------------------------------------------- #
# 出题侧错因记忆召回（模块 F：记忆×出题闭环，2026-09-29）
# --------------------------------------------------------------------------- #
# 召回窗口：取该 kp 最近 N 条作答事件（借鉴 CC memdir.findRelevantMemories——
# 记忆不是全量搬，按 kp 相关性召回最近窗口注入）。
RECALL_WINDOW = 8

# 错题口径（与 project_mistakes 逐字一致）：result='wrong' 或自评「部分会」。
def _is_mistake_row(mode: str, result: str) -> bool:
    return result == "wrong" or (mode == "selfassess" and result == "partial")


# attribute.py 归因失败时落库的是占位串「未归因」（低置信兜底，非真实错因）——
# 记忆侧与 None 同等处理：不进 attribution_counts（否则块内出现「高频错因：未归因×1」
# 的语义噪声）；摘录行由 format_memory_block 兜底标注「未归因」。
_ATTR_PLACEHOLDER = "未归因"


def _real_attr_type(attr) -> str | None:
    """提取真实错因类型：None/空串/占位串「未归因」一律归一为 None。"""
    t = attr.get("type") if isinstance(attr, dict) else None
    if isinstance(t, str):
        t = t.strip()
        if t and t != _ATTR_PLACEHOLDER:
            return t
    return None


def recall_for_generation(conn, kp: str, limit: int = 3,
                          exclude_event_ids=None) -> dict | None:
    """召回该 kp 的学生错因记忆，供出题侧注入 prompt（模块 F2，纯只读）。

    ⚠️ 红线重申：本函数只认传入的 conn，sim 隔离由 conn 决定——模拟器调用方必须
    传入指向 mathforge_sim.db 的独立连接，绝不混入默认库 mathforge.db（与模块头
    注释同口径）；本函数不做任何写入。

    口径：
    - 窗口：该 kp 最近 RECALL_WINDOW(=8) 条作答事件，result IN (correct,wrong,partial)，
      排除 mode IN (override,retry)——与 project_mastery 的取数口径一致；
      不按 sim 过滤（与既有投影一致，隔离靠 conn）。
    - 错题口径 = _is_mistake_row（与 project_mistakes 一致）；"wrong" 字段即该口径计数。
    - 归因复用 _resolve_attribution：独立 override 事件 > 事件自身 user_override > attribution；
      占位串「未归因」（attribute.py 归因失败兜底）与 None 同义——不进 attribution_counts，
      摘录行标注「未归因」（见 _real_attr_type）。
    - recent_wrongs[].q/ans 来自事件 meta 的 F1 落库键（题面/学生答案截断）：
      旧事件 meta 无此键 → 键缺省（省略），绝不报错。
    - exclude_event_ids：仅从 recent_wrongs 摘录中剔除的事件 id 集合。变式场景传源题
      事件 id——源题题面已随 variant_of 进【变式要求】，再进记忆摘录就是同一题面双重
      注入；被剔除事件仍计入 n/wrong/attribution_counts（归因统计不含题面，无重复问题）。
    - pattern_card：patterns_get(conn, pack, kp) 的 pattern_md。recall 只收 kp 参数，
      pack 取窗口内最近一条事件的 pack 列（同一 kp 的事件 pack 恒定，现实中无歧义；
      pack 为空串时跳过查询）。

    返回 None 当该 kp 无任何作答事件（调用侧零注入、prompt 与无记忆版逐字节一致）；
    否则返回：
      {"n": 窗口内事件数, "wrong": 窗口内错题数,
       "attribution_counts": {错因类型: 次数},   # 仅错题参与计数；未归因/占位串不计
       "recent_wrongs": [{day, attribution_type, q?, ans?}, ...],  # 最近 limit 条错题，新→旧
       "pattern_card": str | None}
    """
    rows = conn.execute(
        "SELECT * FROM attempt_events "
        "WHERE kp=? AND result IN ('correct','wrong','partial') "
        "AND mode NOT IN ('override','retry') "
        "ORDER BY ts DESC, id DESC LIMIT ?",
        (kp, RECALL_WINDOW),
    ).fetchall()
    if not rows:
        return None

    override_map = _latest_overrides(conn, None, kp)
    attribution_counts: dict = {}
    recent_wrongs: list[dict] = []
    wrong_n = 0
    for r in rows:
        if not _is_mistake_row(r["mode"], r["result"]):
            continue
        wrong_n += 1
        attr = _resolve_attribution(r, override_map.get(r["id"]))
        attr_type = _real_attr_type(attr)
        if attr_type:
            attribution_counts[attr_type] = attribution_counts.get(attr_type, 0) + 1
        # 摘录剔除（仅摘录，统计仍计入）：变式场景防源题题面双重注入
        excluded = bool(exclude_event_ids) and r["id"] in exclude_event_ids
        if len(recent_wrongs) < max(1, int(limit)) and not excluded:
            entry: dict = {"day": r["day"], "attribution_type": attr_type}
            meta = _parse_json(r["meta"])
            if isinstance(meta, dict):
                # F1 键（q=题面截断, ans=学生答案截断）：旧事件无此键 → 缺省省略
                if meta.get("q"):
                    entry["q"] = str(meta["q"])
                if meta.get("ans"):
                    entry["ans"] = str(meta["ans"])
            recent_wrongs.append(entry)

    pack = rows[0]["pack"] or ""
    pattern_card = None
    if pack:
        card = patterns_get(conn, pack, kp)
        if card:
            pattern_card = card.get("pattern_md")

    return {
        "n": len(rows),
        "wrong": wrong_n,
        "attribution_counts": attribution_counts,
        "recent_wrongs": recent_wrongs,
        "pattern_card": pattern_card,
    }


def format_memory_block(recall: dict | None) -> str:
    """把 recall_for_generation 的结果渲染为「学生错因记忆」文本块（模块 F3）。

    该块由调用侧放进 kp_context["memory_context"]，generate._base_user 仅在非空时
    追加到出题 prompt 末尾。空/无错题历史 → 返回 ""（不注入）：
    - recall 为 None（无任何事件）或 wrong==0（无可瞄准的错因）都返回 ""，
      保证新学生/全对学生的 prompt 与无记忆版逐字节一致（缓存键稳定）。

    红线（写进块内指令）：绝不把学生历史写进题干——记忆只供出题瞄准，不进题面。
    """
    if not recall:
        return ""
    try:
        wrong = int(recall.get("wrong") or 0)
    except (TypeError, ValueError):
        wrong = 0
    if wrong <= 0:
        return ""
    try:
        n = int(recall.get("n") or 0)
    except (TypeError, ValueError):
        n = 0

    lines = ["【学生个人错因记忆（供出题参考，严禁在题干中提及或复述）】"]
    head = f"- 该生近 {n} 次作答：错 {wrong} 次"
    counts = recall.get("attribution_counts") or {}
    if counts:
        top = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:2]
        head += "；高频错因：" + "、".join(f"{t}×{c}" for t, c in top)
    lines.append(head)
    for w in recall.get("recent_wrongs") or []:
        if not isinstance(w, dict):
            continue
        attr_type = w.get("attribution_type") or "未归因"
        seg = f"- 最近错题摘录：【{w.get('day', '')}|{attr_type}】"
        if w.get("q"):
            seg += f"题干摘录：{w['q']}"
        if w.get("ans"):
            seg += f"；学生答：{w['ans']}"
        lines.append(seg)
    card = str(recall.get("pattern_card") or "").strip()
    if card:
        lines.append(f"- 薄弱模式卡：{card[:160]}")
    if counts:
        # 有真实高频错因 → 主指令瞄准高频错因
        aim = "针对高频错因设计陷阱与干扰项"
    else:
        # 全部错题未归因（counts 空，多为 attribute_error 占位/旧事件）→ 「高频错因」
        # 指代悬空，指令降级到最近错题摘录
        aim = "针对最近错题摘录的错因设计陷阱与干扰项"
    lines.append(
        f"- 出题指令：{aim}；变式题优先针对最近一次错因做扰动；"
        "绝不把学生历史写进题干，也不得在题干/解析中提及该生的作答记录。"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _json_or_none(obj):
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, ensure_ascii=False)


def _parse_json(s):
    if s is None:
        return None
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None
