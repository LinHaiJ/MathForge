# -*- coding: utf-8 -*-
"""模块 K（盲评工具，2026-09-29）：2AFC 盲评题集组装 + 结果复算。

目的：验证「AI 变式是否像出题团队出的题」。协议（与用户确认）：
  每道真题（锚池原题）配两个变式——AI 变式（generate 绿标验证链）+ 同源换数变式
  （确定性族对照；无族考点如实降级为第二道 AI 变式并在 manifest 标注），左右位置
  构建期随机；评测者做三件事：2AFC 指认哪边是 AI（+分不出）、五维 rubric 盲打、
  揭盲后填破绽备注。区分率对 50% 基线做 Wilson 95% 区间检验。

红线（本文件即规格）：
  1. API 成本纪律：默认 dry-run 只打印计划与预计调用量（零 API）；`--build` 才真实
     调 LLM，且必须由用户本人在在线态执行。子 agent / 测试一律假 LLM，零真实出网。
  2. demo 红线：盲评构建必须在在线态——启动时检测 demo 态 / 无 DEEPSEEK_API_KEY
     直接报错退出（exit 2），绝不静默降级（dry-run 也不例外：计划必须与可执行态一致）。
     `--score` 例外：纯本地复算已导出的结果 JSON，离线可用。
  3. 不污染默认库：构建全程不写 attempt_events / 任何 db；产物全部落
     blind_eval/session_<ts>/（blind.json 前端盲态数据 + key.json 评分揭盲数据 +
     manifest.json 构建档案 + index.html 页面副本）。
  4. 盲态保证：blind.json 只含题干/选项与匿名 item_id，绝不含 provenance/答案/解析/
     左右归属；这些全部只进 key.json（页面在整对打分提交后才拉取揭盲）。

变式生成路径（全部复用生产链，不另起炉灶）：
  - AI 变式：generate.generate_question(kp_context, qtype="calculation",
    variant_of=原题题干) —— 与 /v2/turn 在线出题同一绿标链（LLM 参数化模板 →
    SymPy 构造 → 盲解对账 + FAKE_KP 问法闸门 → 解析实例化）。variant_of 非空保证
    不路由弱区确定性族（那不是 AI 变式）。
  - 同源换数对照（有族 kp）：v2.v2api._try_pack_family（与生产同判据的包内确定性族，
    零 LLM，实例按 pair 序轮转）→ 降级 generate._FAMILY_KP 根族（generate_from_family，
    仅盲解 advisory + 解析实例化走 LLM）。
  - 无族 kp：对照 = 第二道独立 AI 变式，provenance 仍记 "AI"，manifest 如实标注
    control_kind="ai_second"（该对的 2AFC 是 AI vs AI，解读时注意）。

用法：
  python scripts/build_blind_eval.py --kps "参数方程求导,罗尔定理" --pairs 30        # dry-run
  python scripts/build_blind_eval.py --all --pairs 30 --build                       # 在线态真实构建
  python scripts/build_blind_eval.py --score blind_eval/session_x/results.json      # 离线复算

统计口径：
  区分率 = 指认正确对数 / 决断对数（分不出单列，不入分母）；基线 50%（左右随机下瞎猜）。
  Wilson 95% 区间见 wilson()；五维 rubric 按「被指认为 AI / 被指认为原题 / 分不出」
  三组聚合均分（页面与 --score 同一公式）。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
# v1 只为 import 解析（v2api → policy2 → db 链，eval_ai_variant.py 同例）；不使用 v1 功能
for _p in (str(_ROOT), str(_ROOT / "v2"), str(_ROOT / "v1")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

sys.stdout.reconfigure(encoding="utf-8")

import generate  # noqa: E402  出题链唯一入口（含 demo 判定与 _kp_hit 语义）
import families as root_families  # noqa: E402  根目录确定性族（generate._FAMILY_KP 的实现）
from v2 import pack_loader  # noqa: E402
from v2.v2api import _try_pack_family  # noqa: E402  包内族装配器（eval_ai_variant.py 同例复用）

ANCHOR_POOL = _ROOT / "cache" / "anchor_pool.jsonl"
PAGE_SRC = _ROOT / "blind_eval" / "index.html"
DEFAULT_OUT = _ROOT / "blind_eval"
RUBRIC_DIMS = ["题干表述规范", "条件自洽", "难度与考点匹配", "解析质量", "命题专业度"]
Z95 = 1.959963984540054  # 95% 双侧 z 值
SERVE_PORT = 8139        # 8127=demo / 8128=在线实验，盲评本地服务用 8139
# 每条变式的 LLM 调用估计口径：出题 1 + 盲解对账 1（±复核 1）+ 解析实例化 1 ≈ 2-4 次；
# 失败重生成（attempt 2）至多再翻一倍——报量按 2-4 主口径，重试尾部如实提示。
_CALLS_MIN, _CALLS_MAX = 2, 4


# --------------------------------------------------------------------------- #
# 在线态闸门
# --------------------------------------------------------------------------- #
def ensure_online() -> None:
    """demo 态 / 无 key 直接退出（红线 2，不静默降级）。

    generate._demo_mode() 口径：MATHFORGE_DEMO=1 或无 DEEPSEEK_API_KEY。dry-run 也要
    过闸——计划里的 kp/原题/调用量必须在真实可执行态核对，demo 态的计划是废纸。
    """
    if not generate._demo_mode():
        return
    demo = __import__("os").environ.get("MATHFORGE_DEMO") == "1"
    why = ("MATHFORGE_DEMO=1（演示态，LLM 只读缓存）" if demo
           else "未检测到 DEEPSEEK_API_KEY")
    print(f"[盲评] 拒绝启动：当前为 demo/离线态（{why}）。", file=sys.stderr)
    print("[盲评] 盲评题集构建需要真实调用 LLM 生成 AI 变式，必须在在线态执行：", file=sys.stderr)
    print("       - 确认 mathforge/.env 含 DEEPSEEK_API_KEY；", file=sys.stderr)
    print("       - 不要设置 MATHFORGE_DEMO=1；先跑 dry-run（默认，零 API）核对计划与调用量，", file=sys.stderr)
    print("       - 确认无误后加 --build 由你本人执行。本脚本不静默降级。", file=sys.stderr)
    raise SystemExit(2)


# --------------------------------------------------------------------------- #
# 数据源：锚池 / 包考点 / 族覆盖
# --------------------------------------------------------------------------- #
def load_anchor_pool(path: Path | str | None = None) -> list[dict]:
    """锚池真题（generate._load_jsonl 逐行容错；cache 只读）。"""
    p = Path(path) if path else ANCHOR_POOL
    if not p.exists():
        print(f"[盲评] 锚池不存在：{p}", file=sys.stderr)
        print("       先跑 python scripts/build_anchor_pool.py 产出 cache/anchor_pool.jsonl",
              file=sys.stderr)
        raise SystemExit(2)
    return generate._load_jsonl(p)


def load_kp_index() -> dict:
    """三包 kp_graph 摊平：中文名 → {pack_id, kp_id, name}（--kps 用中文名）。"""
    idx: dict[str, dict] = {}
    for pid in pack_loader.list_packs():
        pack = pack_loader.load_pack(pid)
        for k in pack["kp_graph"]:
            name = str(k.get("name") or "").strip()
            if name and name not in idx:
                idx[name] = {"pack_id": pid, "kp_id": k["id"], "name": name}
    return idx


def anchored_counts(pool: list[dict], kp_names: list[str]) -> dict[str, int]:
    """各 kp 在锚池的真题命中数（generate._kp_hit 同语义：kp 切词任一 token 为子串）。"""
    out = {}
    for name in kp_names:
        out[name] = sum(1 for q in pool if generate._kp_hit(q, name))
    return out


def pick_origin_items(pool: list[dict], kp: str, n: int, used_ids: set[str]) -> list[dict]:
    """选 n 道原题：优先「主观题+有解析」（与变式同为填空形态、rubric 可评解析质量），
    其次主观题，再退任意题型；同内容不重复选用。每档内按 id 排序保证同池确定性。

    容量规划（build_plan）只按主观题计——锚池选择题与填空型变式格式不匹配
    （光凭题型就能识破 AI），绝不入对；tier3 仅作 build 侧防御性兜底。
    """
    hits = [q for q in pool if generate._kp_hit(q, kp) and q.get("id") not in used_ids]
    hits.sort(key=lambda q: str(q.get("id") or ""))
    tiers = (
        [q for q in hits if q.get("type") == "subjective" and (q.get("analysis") or "").strip()],
        [q for q in hits if q.get("type") == "subjective"],
        hits,
    )
    out: list[dict] = []
    for tier in tiers:
        for q in tier:
            if len(out) >= n:
                break
            if q not in out:
                out.append(q)
        if len(out) >= n:
            break
    return out


def count_subjective_origins(pool: list[dict], kp: str) -> int:
    """主观题原题存量（容量口径：选择题真题不与填空变式配对，见 pick_origin_items）。"""
    return sum(1 for q in pool
               if generate._kp_hit(q, kp) and q.get("type") == "subjective")


def pack_family_instances(pack_id: str, kp_id: str, kp_name: str) -> tuple[list[dict] | None, str]:
    """包内确定性族实例（零 LLM）：v2api._try_pack_family 同判据覆盖 → 枚举全部合法格。

    返回 (实例列表 or None, 家族描述)。_try_pack_family 只取单实例，这里为轮转对照
    枚举全集（assert + 渲染守卫过滤与生产一致）。模块覆盖判定与 v2api._module_covers_kp
    同判据：KP_ID 显式优先，KP_NAME + 短名双保险。
    """
    pack = pack_loader.load_pack(pack_id)
    items: list[dict] = []
    fams: list[str] = []
    for mod in (pack.get("families") or {}).values():
        kp_id_attr = getattr(mod, "KP_ID", None)
        covered = kp_id_attr == kp_id
        if not covered:
            short = kp_id.split(".")[-1]
            fname = (getattr(mod, "__name__", "") or "").split(".")[-1]
            fam = getattr(mod, "FAMILY", "") or ""
            covered = bool(kp_name) and getattr(mod, "KP_NAME", None) == kp_name \
                and (short in fname or short in fam)
        if not covered:
            continue
        enum = getattr(mod, "enumerate_family", None)
        if enum is None:
            continue
        for it in enum(limit=24):
            if it and all(bool(v) for _, v in it.get("assert", [])) \
                    and not generate.render_flaws(it.get("statement_md") or "") \
                    and not generate.render_flaws(it.get("analysis") or ""):
                items.append(it)
                fams.append(str(it.get("family") or getattr(mod, "FAMILY", "")))
    if items:
        return items, "+".join(sorted(set(fams)))
    return None, ""


def root_family_instances(kp_name: str) -> tuple[list[dict] | None, str]:
    """根目录族兜底（generate._FAMILY_KP 五考点）：spec 轮转，generate_from_family 实例化
    （盲解 advisory + 解析实例化走 LLM，约 2-3 次/条——计入报量）。"""
    fam = generate._FAMILY_KP.get(kp_name) or next(
        (f for k, f in generate._FAMILY_KP.items() if k in kp_name), None)
    if not fam:
        return None, ""
    specs = root_families.enumerate_family(fam, limit=16)
    return (specs, fam) if specs else (None, "")


# --------------------------------------------------------------------------- #
# 计划（dry-run 与 build 共用）
# --------------------------------------------------------------------------- #
def distribute(pairs: int, capacities: list[int]) -> list[int]:
    """把 N 对按考点轮转分配，超容量截断并把余量再分给有余粮的考点（确定性）。"""
    quota = [0] * len(capacities)
    remaining = pairs
    while remaining > 0:
        progressed = False
        for i, cap in enumerate(capacities):
            if remaining <= 0:
                break
            if quota[i] < cap:
                quota[i] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return quota


def build_plan(args: argparse.Namespace) -> dict:
    """计划：kp 清单 / 原题可得数 / 对照类型 / 配额 / LLM 调用估计。零 API。"""
    pool = load_anchor_pool(args.anchor_pool)
    kp_index = load_kp_index()

    if args.all:
        names = sorted(kp_index.keys())
    else:
        names = [s.strip() for s in str(args.kps or "").split(",") if s.strip()]
        unknown = [n for n in names if n not in kp_index]
        if unknown:
            print(f"[盲评] --kps 含未知考点名（须用 kp_graph 中文名）：{unknown}", file=sys.stderr)
            sample = "、".join(sorted(kp_index.keys())[:12])
            print(f"       示例：{sample} …", file=sys.stderr)
            raise SystemExit(2)
    if not names:
        print("[盲评] 未选择任何考点（--kps 或 --all）。", file=sys.stderr)
        raise SystemExit(2)

    counts = anchored_counts(pool, names)
    plan_kps: list[dict] = []
    used: set[str] = set()
    for name in names:
        info = kp_index[name]
        cnt = counts.get(name, 0)
        entry = {"kp": name, "pack_id": info["pack_id"], "kp_id": info["kp_id"],
                 "anchor_count": cnt}
        if cnt == 0:
            entry["note"] = "锚池零命中，跳过（该考点无真题锚）"
            plan_kps.append(entry)
            continue
        avail = count_subjective_origins(pool, name)
        entry["ori_available"] = avail
        if avail == 0:
            entry["note"] = "锚池真题均为选择题（与填空型变式格式不匹配，光凭题型即识破），跳过"
            plan_kps.append(entry)
            continue
        # 族实例只在 plan 阶段枚举一次（m-10）：build 侧 _gen_control 直接轮转，
        # 不再每对重枚举。私有键（下划线前缀）写 manifest 前剥离，防实例题面/答案入档案。
        entry["_family_desc"] = ""
        pack_insts, desc = pack_family_instances(info["pack_id"], info["kp_id"], name)
        if pack_insts:
            entry["control_kind"] = "family_pack"
            entry["_pack_insts"] = pack_insts
        else:
            root_specs, desc = root_family_instances(name)
            if root_specs:
                entry["control_kind"] = "family_root"
                entry["_root_specs"] = root_specs
            else:
                entry["control_kind"] = "ai_second"
                entry["note"] = "无确定性族：对照为第二道独立 AI 变式（2AFC 为 AI vs AI，解读注意）"
        entry["family_desc"] = desc
        entry["family_instances"] = len(pack_insts or []) + len(
            entry.get("_root_specs") or [])
        entry["capacity"] = min(avail, entry["family_instances"] or avail)
        plan_kps.append(entry)

    capable = [e for e in plan_kps if e.get("anchor_count", 0) > 0]
    # 余量优先：锚多的考点先排（同容量按 kp 名稳定序）——--all 小额 pairs 不会全落在
    # 字典序靠前的冷考点上，分配仍然确定性可复算。
    capable.sort(key=lambda e: (-e["capacity"], e["kp"]))
    capacities = [e["capacity"] for e in capable]
    quota = distribute(args.pairs, capacities)
    for e, q in zip(capable, quota):
        e["pairs_planned"] = q
    for e in plan_kps:
        e.setdefault("pairs_planned", 0)

    # LLM 调用估计：AI 变式每条 2-4；对照：包族 0 / 根族 2-3 / 第二道 AI 2-4
    n_ai = sum(e["pairs_planned"] for e in plan_kps)
    n_root = sum(e["pairs_planned"] for e in plan_kps if e.get("control_kind") == "family_root")
    n_second = sum(e["pairs_planned"] for e in plan_kps if e.get("control_kind") == "ai_second")
    total_min = n_ai * _CALLS_MIN + n_root * 2 + n_second * _CALLS_MIN
    total_max = n_ai * _CALLS_MAX + n_root * 3 + n_second * _CALLS_MAX
    planned_pairs = sum(e["pairs_planned"] for e in plan_kps)
    return {
        "mode": "build" if args.build else "dry_run",
        "pairs_requested": args.pairs,
        "planned_pairs": planned_pairs,
        "difficulty": args.difficulty,
        "out": str(Path(args.out)),
        "seed": args.seed,
        "kps": plan_kps,
        "estimate": {
            "ai_variants": n_ai,
            "controls_family_pack": n_ai - n_root - n_second,
            "controls_family_root": n_root,
            "controls_ai_second": n_second,
            "llm_calls_min": total_min,
            "llm_calls_max": total_max,
        },
    }


def print_plan(plan: dict) -> None:
    print("=" * 72)
    print(f"盲评题集计划（{'--build 真实构建' if plan['mode'] == 'build' else 'dry-run，零 API'}）")
    print("=" * 72)
    print(f"目标对数：{plan['pairs_requested']}（实际可排 {plan['planned_pairs']}，受原题存量限制）"
          f"｜难度：{plan['difficulty']}｜随机种子：{plan['seed']}")
    print(f"输出目录：{plan['out']}\\session_<时间戳>\\（幂等：每次 build 新目录，不覆盖）")
    print("-" * 72)
    print(f"{'考点':<14}{'锚池':>4} {'原题':>4} {'对照':<12} {'族实例':>6} {'计划对':>6}  备注")
    for e in plan["kps"]:
        if e.get("anchor_count", 0) == 0:
            print(f"{e['kp']:<14}{0:>4} {'-':>4} {'-':<12} {'-':>6} {0:>6}  {e.get('note', '')}")
            continue
        kind = {"family_pack": "家族换数", "family_root": "家族换数(根族)",
                "ai_second": "第二道AI*"}.get(e.get("control_kind"), "?")
        print(f"{e['kp']:<14}{e['anchor_count']:>4} {e.get('ori_available', 0):>4} {kind:<12} "
              f"{e.get('family_instances', 0):>6} {e['pairs_planned']:>6}  {e.get('note', '')}")
    est = plan["estimate"]
    print("-" * 72)
    print(f"预计 LLM 调用：约 {est['llm_calls_min']}–{est['llm_calls_max']} 次"
          f"（AI 变式 {est['ai_variants']} 条 ×2-4：出题+盲解对账±复核+解析实例化；"
          f"根族对照 {est['controls_family_root']} 条 ×2-3；第二道 AI 对照 {est['controls_ai_second']} 条 ×2-4；"
          f"包族对照 {est['controls_family_pack']} 条 ×0）")
    print("成本口径：以上为 DeepSeek chat 调用次数（每次出题约 1.5-3k tokens、盲解约 0.5-1k），")
    print("          按你账号的现价折算；失败重生成至多再翻一倍。缓存命中的调用零费用。")
    if any(e.get("control_kind") == "ai_second" and e["pairs_planned"] for e in plan["kps"]):
        print("* 无族考点的对照是第二道 AI 变式（manifest 已如实标注）：这些对的 2AFC 是")
        print("  「AI vs AI」，区分率解读时请结合 manifest 的 control_kind 分桶。")
    if plan["mode"] == "build":
        print("-" * 72)
        print("确认无误后此命令即开始真实构建（在线态）。进度逐对打印，产物落 session 目录。")
    else:
        print("-" * 72)
        print("以上为 dry-run，未调任何 API。加 --build 在在线态真实构建（由你本人执行）。")


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
def _norm_options(options) -> "list[str] | None":
    """选项归一为字符串列表（锚池选项可能是 {label, content_md} 字典）。"""
    if not options:
        return None
    out = []
    for o in options:
        if isinstance(o, dict):
            out.append(f"{o.get('label', '')}. {o.get('content_md', '')}".strip())
        else:
            out.append(str(o))
    return out or None


def _masked(item_id: str, statement: str, options) -> dict:
    """盲态条目：只有题面，绝无答案/解析/来源。"""
    return {"item_id": item_id, "statement_md": statement or "", "options": _norm_options(options)}


def _answer_of(q: dict) -> str:
    return str(q.get("answer_sympy") or q.get("answer_md") or q.get("correct") or "")


def _gen_ai_variant(kp_name: str, ori_stmt: str, difficulty: str) -> dict:
    """AI 变式：生产绿标链（variant_of 非空 ⇒ 不路由弱区族）。blocked 原样返回。"""
    return generate.generate_question({"kp": kp_name}, difficulty=difficulty,
                                      qtype="calculation",
                                      variant_of=(ori_stmt or "")[:240])


def _gen_control(kp_name: str, e: dict, idx: int, difficulty: str) -> tuple[dict | None, str, str]:
    """对照变式：plan 阶段已枚举的族实例轮转（m-10，零重复枚举）→ 无族则 None（调用方
    兜底第二道 AI）。包族实例零 LLM；根族 spec 仍经 generate_from_family 实例化
    （盲解 advisory + 解析实例化走 LLM）。"""
    pack_insts = e.get("_pack_insts") or []
    if pack_insts:
        return dict(pack_insts[idx % len(pack_insts)]), "family_pack", e.get("family_desc", "")
    root_specs = e.get("_root_specs") or []
    if root_specs:
        q = generate.generate_from_family(root_specs[idx % len(root_specs)])
        return q, "family_root", e.get("family_desc", "")
    return None, "ai_second", ""


def _valid(q: dict | None) -> bool:
    return bool(q) and (q.get("statement_md") or "").strip() != "" \
        and q.get("status") != "blocked_pending_human"


def run_build(plan: dict, args: argparse.Namespace) -> "Path | None":
    """真实构建（在线态）。逐对生成 → 组对 → 随机左右 → 拆 blind/key 落盘。
    全部对被跳过时不落 session（返回 None 并给出人话提示，m-5）。"""
    pool = load_anchor_pool(args.anchor_pool)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session = f"session_{ts}"
    k = 1
    while (out_root / session).exists():
        k += 1
        session = f"session_{ts}_{k}"
    sdir = out_root / session
    sdir.mkdir(parents=True)

    if not PAGE_SRC.exists():
        print(f"[盲评] 页面缺失：{PAGE_SRC}（盲评页应随仓库交付）", file=sys.stderr)
        raise SystemExit(2)

    seed = args.seed if args.seed is not None else random.randrange(2 ** 31)
    rng = random.Random(seed)
    plan_by_kp = {e["kp"]: e for e in plan["kps"]}

    blind_pairs: list[dict] = []
    key_pairs: list[dict] = []
    used_ids: set[str] = set()
    skipped: list[dict] = []

    todo = [(e["kp"], e["pairs_planned"]) for e in plan["kps"] if e["pairs_planned"] > 0]
    total = sum(q for _, q in todo)
    done = 0
    print(f"[盲评] 构建 {total} 对 → {sdir}")
    for kp_name, quota in todo:
        e = plan_by_kp[kp_name]
        oris = pick_origin_items(pool, kp_name, quota, used_ids)
        if len(oris) < quota:
            skipped.append({"kp": kp_name, "reason": f"原题不足（要 {quota} 得 {len(oris)}）"})
        for i, ori in enumerate(oris):
            done += 1
            pair_id = f"p{done:03d}"
            tag = f"[{done}/{total}] {kp_name} {pair_id}"

            ai_q = _gen_ai_variant(kp_name, ori.get("statement_md") or "", plan["difficulty"])
            if not _valid(ai_q):
                skipped.append({"pair_id": pair_id, "kp": kp_name,
                                "reason": f"AI 变式未过验证链：{(ai_q or {}).get('error') or 'blocked'}"})
                print(f"{tag} ✗ AI 变式被拦截，跳过（{str((ai_q or {}).get('error'))[:60]}）")
                continue
            if (ai_q.get("statement_md") or "").strip() == (ori.get("statement_md") or "").strip():
                skipped.append({"pair_id": pair_id, "kp": kp_name,
                                "reason": "AI 变式与母本题干相同（判作无效对，跳过）"})
                print(f"{tag} ✗ AI 变式与母本撞题，跳过")
                continue

            ctrl_q, ctrl_kind, fam_desc = _gen_control(kp_name, e, i, plan["difficulty"])
            if ctrl_q is None:  # 无族 → 第二道独立 AI 变式（manifest 如实标注）
                ctrl_q = _gen_ai_variant(kp_name, ori.get("statement_md") or "", plan["difficulty"])
                ctrl_kind = "ai_second"
            if not _valid(ctrl_q):
                skipped.append({"pair_id": pair_id, "kp": kp_name,
                                "reason": f"对照变式未过验证链：{(ctrl_q or {}).get('error') or 'blocked'}"})
                print(f"{tag} ✗ 对照变式被拦截，跳过")
                continue
            ctrl_stmt = (ctrl_q.get("statement_md") or "").strip()
            if ctrl_stmt == (ai_q.get("statement_md") or "").strip():
                skipped.append({"pair_id": pair_id, "kp": kp_name,
                                "reason": "AI 变式与对照题干完全相同（判作无效对，跳过）"})
                print(f"{tag} ✗ 变式与对照撞题，跳过")
                continue
            if ctrl_stmt == (ori.get("statement_md") or "").strip():
                skipped.append({"pair_id": pair_id, "kp": kp_name,
                                "reason": "对照与母本题干相同（判作无效对，跳过）"})
                print(f"{tag} ✗ 对照与母本撞题，跳过")
                continue

            used_ids.add(ori.get("id"))
            ai_left = rng.random() < 0.5  # 左右随机：构建期定死，记录只进 key
            left, right = (ai_q, ctrl_q) if ai_left else (ctrl_q, ai_q)
            ids = {"ori": f"{pair_id}-O", "left": f"{pair_id}-L", "right": f"{pair_id}-R"}

            # provenance 定案：AI 变式恒 "AI"；对照在族路径（包族/根族）记 "family"，
            # 无族降级的第二道 AI 变式如实记 "AI"（manifest.control_kind 已标注，解读分桶）。
            ctrl_is_family = ctrl_kind in ("family_pack", "family_root")
            ctrl_prov = "family" if ctrl_is_family else "AI"
            ctrl_desc = (f"同源换数·{fam_desc}" if ctrl_is_family
                         else "第二道独立 AI 变式（无族对照）")
            ai_desc = f"AI 变式·绿标链(variant_of={ori.get('id')})"

            blind_pairs.append({
                "pair_id": pair_id, "kp": kp_name,
                "ori": _masked(ids["ori"], ori.get("statement_md"), ori.get("options")),
                "left": _masked(ids["left"], left.get("statement_md"), left.get("options")),
                "right": _masked(ids["right"], right.get("statement_md"), right.get("options")),
            })

            def _key_item(q: dict, item_id: str, prov: str, desc: str) -> dict:
                return {"item_id": item_id, "provenance": prov, "source_desc": desc,
                        "answer": _answer_of(q), "analysis": str(q.get("analysis") or ""),
                        "verify_level": q.get("verify_level"),
                        "family": q.get("family"), "params": q.get("params")}

            key_pairs.append({
                "pair_id": pair_id, "kp": kp_name,
                "ai_side": "left" if ai_left else "right",
                "control_kind": ctrl_kind,
                "left": _key_item(left, ids["left"],
                                  "AI" if ai_left else ctrl_prov,
                                  ai_desc if ai_left else ctrl_desc),
                "right": _key_item(right, ids["right"],
                                   ctrl_prov if ai_left else "AI",
                                   ctrl_desc if ai_left else ai_desc),
                "ori": {"item_id": ids["ori"], "provenance": "ori",
                        "answer": str(ori.get("answer") or ""),
                        "analysis": str(ori.get("analysis") or ""),
                        "anchor_id": ori.get("id"), "source_raw": ori.get("source_raw")},
            })
            side = "左" if ai_left else "右"
            ctrl_show = fam_desc if ctrl_is_family else "第二道AI"
            print(f"{tag} ✓ AI 在{side}｜对照={ctrl_show}")

    manifest = {
        "tool": "build_blind_eval.py",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "seed": seed,
        "difficulty": plan["difficulty"],
        "requested_pairs": plan["pairs_requested"],
        "built_pairs": len(blind_pairs),
        # 私有键（_pack_insts/_root_specs，族实例题面）不入档案（m-10）
        "kps": [{k: v for k, v in e.items() if not k.startswith("_")}
                for e in plan["kps"]],
        "estimate": plan["estimate"],
        "skipped": skipped,
        "control_kind_note": ("control_kind=ai_second 的对：对照也是 AI 变式（无确定性族），"
                              "2AFC 区分率解读需分桶（见 --score 与结果页分桶统计）"),
        "files": {"blind.json": "前端盲态数据（无答案/来源/左右归属）",
                  "key.json": "评分揭盲数据（provenance/答案/解析/ai_side）",
                  "index.html": "盲评页副本（file:// 受限时用 http.server）"},
    }
    if not blind_pairs:
        # m-5：全跳过不落空 session——清掉目录，把跳过原因讲清楚，避免空壳误导
        shutil.rmtree(sdir, ignore_errors=True)
        print("-" * 72, file=sys.stderr)
        print(f"[盲评] 警告：0 对可评（跳过 {len(skipped)}），已清理空 session 目录，未落任何产物。",
              file=sys.stderr)
        for s in skipped:
            print(f"       跳过 {s.get('pair_id', s.get('kp', '?'))}：{s['reason'][:80]}",
                  file=sys.stderr)
        print("[盲评] 常见原因：AI 变式未过验证链（弱区考点）/ 锚池主观题不足 / 撞题。",
              file=sys.stderr)
        print("[盲评] 建议：换有族考点（dry-run 表中「对照=家族换数」）或减少 --pairs 后重试。",
              file=sys.stderr)
        return None

    (sdir / "blind.json").write_text(json.dumps(
        {"session": session, "generated_at": manifest["created_at"],
         "rubric_dims": RUBRIC_DIMS, "pairs": blind_pairs},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (sdir / "key.json").write_text(json.dumps(
        {"session": session, "pairs": key_pairs}, ensure_ascii=False, indent=1), encoding="utf-8")
    (sdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    shutil.copyfile(PAGE_SRC, sdir / "index.html")

    print("-" * 72)
    print(f"[盲评] 完成：{len(blind_pairs)} 对（跳过 {len(skipped)}），session={session}")
    for s in skipped:
        print(f"       跳过 {s.get('pair_id', s.get('kp', '?'))}：{s['reason'][:80]}")
    print("[盲评] 开始盲评（本地静态页，file:// 受限时用 http.server）：")
    print(f"       cd \"{sdir}\"")
    print(f"       python -m http.server {SERVE_PORT}")
    print(f"       浏览器打开 http://127.0.0.1:{SERVE_PORT}/index.html")
    print(f"[盲评] 评完在结果页导出 results JSON，可离线复算：")
    print(f"       python scripts/build_blind_eval.py --score \"{sdir / 'results.json'}\"")
    return sdir


# --------------------------------------------------------------------------- #
# 统计（页面与 --score 同一公式）
# --------------------------------------------------------------------------- #
def wilson(x: int, n: int, z: float = Z95) -> "tuple[float, float] | None":
    """Wilson 得分区间（95%）。n=0 返回 None；x=0/n 端点数学上恰为 0/1（显式钳制防浮点差）。"""
    if n <= 0:
        return None
    p = x / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    lo = 0.0 if x <= 0 else max(0.0, center - half)
    hi = 1.0 if x >= n else min(1.0, center + half)
    return lo, hi


def _bucket_stats(b: dict) -> dict:
    """单桶统计：decisive/correct/unsure → 区分率 + Wilson 95%（与页面同公式）。"""
    d, c, u = b["decisive"], b["correct"], b["unsure"]
    rate = (c / d) if d else None
    lo, hi = wilson(c, d) or (None, None)
    return {"decisive": d, "correct": c, "unsure": u,
            "detection_rate": round(rate, 4) if rate is not None else None,
            "wilson95": [round(lo, 4), round(hi, 4)] if d else None}


def aggregate_results(results: list[dict], key_by_pair: dict,
                      stmt_by_item: "dict[str, str] | None" = None) -> dict:
    """页面导出 results（与 key.json 对齐）→ 区分率 + Wilson + 五维分组均分。

    results 条目：{pair_id, choice: left|right|unsure,
                   rubric: {item_id: {dim: 1-5}}, note: str}（缺 choice 的对跳过）。
    stmt_by_item：item_id → 题干（来自 blind.json，破绽清单摘要用；可选）。

    分桶（M-2）：按 key 的 control_kind 分 {all, family, ai_second} 三档——ai_second
    对（对照也是 AI）不混入 family 主口径；顶层 decisive/correct/detection_rate/
    wilson95 即 all 桶（旧字段兼容）。pairs_scored = 决断 + 分不出（m-3：未作答对与
    key 中无对应的 ghost 结果均不计入，ghost 数另见 results_not_in_key）。
    """
    stmt_by_item = stmt_by_item or {}
    buckets = {"all": {"decisive": 0, "correct": 0, "unsure": 0},
               "family": {"decisive": 0, "correct": 0, "unsure": 0},
               "ai_second": {"decisive": 0, "correct": 0, "unsure": 0}}
    rub = {"judged_ai": {}, "judged_family": {}, "unsure": {}}
    flaws: list[dict] = []
    not_in_key = 0
    for res in results:
        key = key_by_pair.get(res.get("pair_id"))
        if not key:
            not_in_key += 1
            continue
        choice = res.get("choice")
        if choice not in ("left", "right", "unsure"):
            continue  # 未作答：不进任何计数（m-3）
        bk = "ai_second" if key.get("control_kind") == "ai_second" else "family"
        if choice == "unsure":
            buckets["all"]["unsure"] += 1
            buckets[bk]["unsure"] += 1
        else:
            buckets["all"]["decisive"] += 1
            buckets[bk]["decisive"] += 1
            if choice == key.get("ai_side"):
                buckets["all"]["correct"] += 1
                buckets[bk]["correct"] += 1
                ai_item = key.get(choice) or {}
                flaws.append({
                    "pair_id": key["pair_id"], "kp": key["kp"],
                    "side": choice,
                    "statement": stmt_by_item.get(ai_item.get("item_id") or "", "")[:120],
                    "note": str(res.get("note") or ""),
                })
        chosen = (key.get(choice) or {}).get("item_id") if choice in ("left", "right") else None
        for item_id, dims in (res.get("rubric") or {}).items():
            # 分组按「题」不按「对」：被指认为 AI 的题 vs 被指认为原题的题（该对另一道）；
            # 分不出的对两道都进 unsure 组。
            if choice == "unsure":
                g = "unsure"
            elif chosen:
                g = "judged_ai" if item_id == chosen else "judged_family"
            else:
                continue
            bucket = rub.setdefault(g, {})
            for dim, val in dims.items():
                try:
                    v = float(val)
                except (TypeError, ValueError):
                    continue
                if not (1 <= v <= 5):
                    continue
                acc = bucket.setdefault(dim, {"sum": 0.0, "n": 0})
                acc["sum"] += v
                acc["n"] += 1
    rubric_means = {
        g: {dim: (round(a["sum"] / a["n"], 3) if a["n"] else None)
            for dim, a in dims.items()}
        for g, dims in rub.items()
    }
    rubric_counts = {
        g: {dim: a["n"] for dim, a in dims.items()} for g, dims in rub.items()
    }
    out = {k: _bucket_stats(v) for k, v in buckets.items()}
    return {
        "pairs_scored": out["all"]["decisive"] + out["all"]["unsure"],
        "decisive": out["all"]["decisive"],
        "correct": out["all"]["correct"],
        "unsure": out["all"]["unsure"],
        "detection_rate": out["all"]["detection_rate"],
        "wilson95": out["all"]["wilson95"],
        "baseline": 0.5,
        "buckets": out,
        "results_not_in_key": not_in_key,
        "rubric_means": rubric_means,
        "rubric_counts": rubric_counts,
        "flaws": flaws,
    }


def _load_json_or_exit(path: Path, what: str):
    """读 JSON，坏文件给人话报错不裸栈（m-6）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        print(f"[盲评] {what} 无法解析（损坏/非 JSON）：{path}", file=sys.stderr)
        print(f"       {e}", file=sys.stderr)
        raise SystemExit(2) from e


def run_score(results_path: Path) -> dict:
    """离线复算：results.json（页面导出）+ 同目录 key.json/blind.json → 统计。

    纯本地零 API 零网络（--score 是唯一免在线闸门的模式：评完 offline 复算合法）。
    坏 results.json → 人话报错退出；results 与 key 的 session 不一致或结果引用了
    key 里不存在的 pair → 告警（仍对能对上的部分出统计，m-6）。
    """
    sdir = results_path.resolve().parent
    key_path = sdir / "key.json"
    blind_path = sdir / "blind.json"
    if not results_path.exists() or not key_path.exists():
        print(f"[盲评] 缺文件：{results_path} 或 {key_path}", file=sys.stderr)
        print("       results.json 由盲评页结果页「下载结果 JSON」导出到 session 目录。",
              file=sys.stderr)
        raise SystemExit(2)
    data = _load_json_or_exit(results_path, "results.json")
    key = _load_json_or_exit(key_path, "key.json")
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        print("[盲评] results.json 结构不对：预期页面导出的 {\"session\":…, \"results\":[…]}。",
              file=sys.stderr)
        raise SystemExit(2)
    key_pairs = key.get("pairs") if isinstance(key, dict) else None
    if not isinstance(key_pairs, list):
        print("[盲评] key.json 结构不对：预期 {\"session\":…, \"pairs\":[…]}。", file=sys.stderr)
        raise SystemExit(2)
    key_by_pair = {p.get("pair_id"): p for p in key_pairs if isinstance(p, dict)}
    r_sess = data.get("session")
    k_sess = key.get("session") if isinstance(key, dict) else None
    if r_sess and k_sess and r_sess != k_sess:
        print(f"[盲评] 告警：results.json 的 session（{r_sess}）与 key.json 的 session"
              f"（{k_sess}）不一致——结果可能来自另一次构建，仅对 pair_id 能对上的部分出统计。",
              file=sys.stderr)
    stmt_by_item: dict[str, str] = {}
    if blind_path.exists():
        blind = _load_json_or_exit(blind_path, "blind.json")
        for p in (blind.get("pairs") or []) if isinstance(blind, dict) else []:
            for side in ("ori", "left", "right"):
                it = p.get(side) or {}
                if it.get("item_id"):
                    stmt_by_item[it["item_id"]] = str(it.get("statement_md") or "")
    stats = aggregate_results(list(data["results"]), key_by_pair, stmt_by_item)
    if stats["results_not_in_key"]:
        print(f"[盲评] 告警：{stats['results_not_in_key']} 条结果在 key.json 中无对应 pair"
              f"（跨 session 错配或 key 缺失），已忽略。", file=sys.stderr)
    stats["session"] = r_sess or sdir.name
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    if stats["flaws"]:
        print("-" * 72)
        print("破绽清单（被识破的 AI 题）：")
        for f in stats["flaws"]:
            note = f"｜备注：{f['note']}" if f["note"] else ""
            print(f"  {f['pair_id']} {f['kp']}｜{f['statement'][:60]}…{note}")
    return stats


# --------------------------------------------------------------------------- #
def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="盲评题集组装（默认 dry-run 零 API；--build 在线态真实构建）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--kps", help="逗号分隔的考点中文名（kp_graph name），如 \"参数方程求导,罗尔定理\"")
    src.add_argument("--all", action="store_true", help="选全部有锚考点")
    ap.add_argument("--pairs", type=int, default=30, help="目标对数（默认 30）")
    ap.add_argument("--difficulty", default="基础", choices=["基础", "进阶", "综合"])
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出根目录（默认 blind_eval/）")
    ap.add_argument("--seed", type=int, default=None, help="左右随机的随机种子（默认随机）")
    ap.add_argument("--build", action="store_true", help="真实构建（在线态；默认 dry-run）")
    ap.add_argument("--score", default=None, help="离线复算页面导出的 results.json（免在线闸门）")
    ap.add_argument("--anchor-pool", default=None, help=argparse.SUPPRESS)  # 测试注入假池用
    args = ap.parse_args(argv)

    if args.score:
        run_score(Path(args.score))
        return 0

    ensure_online()
    plan = build_plan(args)
    print_plan(plan)
    if args.build:
        run_build(plan, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
