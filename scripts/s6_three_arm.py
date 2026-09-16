"""S6 三臂 A/B 实验（完成判据 §7）：无 skill / 全量 skill / 分层 skill × LLM 子集分域。

设计（沿用 ab_skill_ablation.py 三层骨架，扩为三臂 + 分域表）：
- 臂①「无 skill」：system prompt 无出题规范（空串）—— 等价于 ab 的 B 臂。
- 臂②「全量 skill」：skills/math-examiner/SKILL.md 现状（v1 出题专家）—— 等价于 ab 的 A 臂。
- 臂③「分层 skill」：pack_loader.examiner_prompt（core 全局契约 + 所属包域段拼接）。

评测计划：复用 R6/ab 的 big40 计划（cache/ab_skill_ablation.json 同源），即
  家族确定性路径 24 例（对照组，应与 skill 无关而三臂一致）+ LLM 路径 16 例（核心测量）。

预算纪律：臂①②直接复用 ab 历史缓存（零新增 API 调用，cache_hit=true）；
臂③使用全新命名空间 gen-green-s6:v1（cache_hit=false），真实调用或缓存预热，
单次实验规模 ≤¥5。无 key 或 namespace 全 miss 时 → 仅出可命中臂 + 记录 degraded，不阻塞。

红线：结果只记 kp 名与计数，绝不写题干/答案原文。报告只陈述数字，不预设结论。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(ROOT) / "v1"))  # eval 迁入 v1/
sys.path.insert(0, str(Path(ROOT) / "v2"))  # pack_loader 迁入 v2/

import eval as EV          # _load_kp / wilson_ci / KP_* 常量
import generate            # generate_question / load_skill / _NS_GREEN
import pack_loader         # load_pack / list_packs

AB_CACHE = ROOT / "cache" / "ab_skill_ablation.json"
OUT = ROOT / "cache" / "s6_result.json"
DOMAINS = ["calculus", "linear", "probability"]

# ---------------------------------------------------------------------------
# 1. 计划（与 eval.m1_green_success "big40" 完全一致，保证三臂同源可比）
# ---------------------------------------------------------------------------
def _plan() -> tuple[list, list]:
    rolle = EV._load_kp(EV.KP_ZWL, "罗尔")
    lagr = EV._load_kp(EV.KP_ZWL, "拉格朗日")
    cauchy = EV._load_kp(EV.KP_ZWL, "柯西")
    taylor = EV._load_kp(EV.KP_ZWL, "泰勒")
    inv = EV._load_kp(EV.KP_XD, "逆矩阵")
    mul = EV._load_kp(EV.KP_XD, "矩阵乘法")
    rank = EV._load_kp(EV.KP_XD, "矩阵的秩")
    cond = EV._load_kp(EV.KP_GL, "条件概率公式")
    addp = EV._load_kp(EV.KP_GL, "互不相容事件的加法公式")
    prop = EV._load_kp(EV.KP_GL, "条件概率的性质")
    sub = EV._load_kp(EV.KP_GL, "减法公式")
    add3 = EV._load_kp(EV.KP_GL, "三个事件的加法公式")
    family = [(rolle, "基础")] * 6 + [(lagr, "进阶")] * 6 + \
             [(mul, "基础")] * 6 + [(inv, "进阶")] * 6
    llm = [(cond, "基础"), (cond, "进阶"), (addp, "基础"), (addp, "进阶"),
           (cauchy, "基础"), (cauchy, "进阶"), (taylor, "基础"), (taylor, "进阶"),
           (rank, "基础"), (rank, "进阶"),
           (prop, "基础"), (prop, "进阶"), (sub, "基础"), (sub, "进阶"),
           (add3, "基础"), (add3, "进阶")]
    return family, llm


# ---------------------------------------------------------------------------
# 2. kp → pack 映射（分域口径）
#    - 自动：中文 kp 名与 packs/*/kp_graph.json 的 name 双向子串匹配
#    - 回退：已知域归属手动表（柯西/泰勒→calculus 等，pack kp_graph 未收录中文全名）
#    - 仍不中 → unmapped（进整体表，不进分域表）
# ---------------------------------------------------------------------------
def _build_kp_to_pack():
    name_to_pack: dict[str, str] = {}
    for pid in pack_loader.list_packs():
        pack = pack_loader.load_pack(pid)
        for k in pack["kp_graph"]:
            name_to_pack[k["name"]] = pid
    manual = {
        "柯西中值定理": "calculus", "泰勒定理": "calculus", "矩阵的秩": "linear",
        "条件概率公式": "probability", "互不相容事件的加法公式": "probability",
        "条件概率的性质": "probability", "减法公式": "probability",
        "三个事件的加法公式": "probability",
    }

    def map_kp(kp: str) -> tuple[str | None, str]:
        for name, pid in name_to_pack.items():
            if kp == name or kp in name or name in kp:
                return pid, "auto"
        if kp in manual:
            return manual[kp], "manual"
        return None, "unmapped"

    return map_kp


# ---------------------------------------------------------------------------
# 3. 统计工具
# ---------------------------------------------------------------------------
def _sanitize(case: dict) -> dict:
    # generate_question 原始返回无 "ok" 字段；eval.m1_green_success 才计算。
    # 因此 ok 优先取显式字段（ab 历史缓存含 ok），否则由 statement_md 推导。
    ok = case.get("ok")
    if ok is None:
        ok = bool(case.get("statement_md"))
    return {"kp": case.get("kp"), "difficulty": case.get("difficulty"),
            "routed": case.get("routed"), "ok": bool(ok)}


def _stats(cases: list) -> dict:
    n = len(cases)
    ok = sum(1 for c in cases if c["ok"])
    ci = list(EV.wilson_ci(ok, n)) if n else None
    return {"n": n, "ok": ok,
            "rate": round(ok / n, 3) if n else None,
            "ci95": ci}


def _by_domain(llm_cases: list, map_kp) -> dict:
    buckets = {d: [] for d in DOMAINS + ["unmapped"]}
    sources = {d: [] for d in DOMAINS + ["unmapped"]}
    for c in llm_cases:
        pid, src = map_kp(c["kp"])
        key = pid if pid else "unmapped"
        buckets[key].append(c)
        sources[key].append(src)
    out = {k: _stats(v) for k, v in buckets.items()}
    out["_source"] = sources  # 仅内部记录映射来源，落盘前剥离
    return out


# ---------------------------------------------------------------------------
# 4. 臂③生成（分层 skill）
# ---------------------------------------------------------------------------
def _run_arm3(family_plan, llm_plan, map_kp, pack_prompts, core_only, budget_calls=300):
    """真实生成臂③。返回 (cases, new_calls, degraded_reason)。"""
    from llm import CACHE_DIR
    before = set(p.name for p in CACHE_DIR.glob("*.json"))

    # 注入分层 skill，并按 kp 所属包选域段；namespace 隔离为 gen-green-s6:v2
    # （v1 已被本脚本调试/预跑污染，按 K5 毒缓存纪律换新命名空间重跑，保证非毒缓存）
    holder = {"value": ""}
    generate.load_skill = lambda: holder["value"]          # 运行时按 kp 切域段
    generate._NS_GREEN = "gen-green-s6:v2:a{}:"
    generate._NS_YELLOW = "gen-yellow-s6:v2:a{}:"

    cases = []
    degraded = None
    try:
        for kp_ctx, diff in family_plan + llm_plan:
            holder["value"] = pack_prompts.get(map_kp(kp_ctx["kp"])[0]) or core_only
            q = generate.generate_question(kp_ctx, difficulty=diff, qtype="calculation")
            cases.append(_sanitize(q))
            # 预算硬闸：超过预算调用数即停，记录 partial
            after = set(p.name for p in CACHE_DIR.glob("*.json"))
            if len(after - before) > budget_calls:
                degraded = f"预算硬闸：新增调用 {len(after - before)} > {budget_calls}，剩余用例标记 partial"
                break
    except Exception as e:  # noqa: BLE001 —— 任何异常 → 降级不阻塞
        degraded = f"臂③生成异常（已尽力收集）：{type(e).__name__}: {str(e)[:160]}"

    after = set(p.name for p in CACHE_DIR.glob("*.json"))
    new_calls = len(after - before)
    return cases, new_calls, degraded


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    t0 = time.time()
    family_plan, llm_plan = _plan()
    map_kp = _build_kp_to_pack()

    notes = []
    degraded_arm3 = None

    # --- 臂①②：复用 ab 历史缓存（零新增调用） ---
    if not AB_CACHE.exists():
        notes.append("ab_skill_ablation.json 缺失；臂①②改走 generate_question 即时复用缓存命名空间（若缓存缺失且 key 缺失则降级）。")
        raise SystemExit("当前实现要求 cache/ab_skill_ablation.json 存在（与任务书『复用 R6/ab 计划』一致）；缺失请先跑 ab 实验。")
    ab = json.load(open(AB_CACHE, encoding="utf-8"))
    no_skill_cases = [_sanitize(c) for c in ab["B_cases"]]   # B 臂 = 无 skill
    full_skill_cases = [_sanitize(c) for c in ab["A_cases"]]  # A 臂 = 全量 skill

    # --- 臂③：分层 skill，真实生成 ---
    pack_prompts = {pid: pack_loader.load_pack(pid)["examiner_prompt"] for pid in DOMAINS}
    core_only = (ROOT / "skills" / "_core" / "examiner-core.md").read_text(encoding="utf-8")
    arm3_cases, arm3_calls, arm3_deg = _run_arm3(
        family_plan, llm_plan, map_kp, pack_prompts, core_only)
    if arm3_deg:
        degraded_arm3 = arm3_deg
        notes.append(f"臂③ degraded：{arm3_deg}")

    # --- 拆分 LLM / family ---
    def split(cases):
        llm = [c for c in cases if c.get("routed") != "family"]
        fam = [c for c in cases if c.get("routed") == "family"]
        return llm, fam

    nllm, ns = split(no_skill_cases)    # split 返回 (llm, fam) → nllm=LLM, ns=家族
    fllm, fs = split(full_skill_cases)
    t3llm, t3s = split(arm3_cases)

    arms = {
        "no_skill": {"cache_hit": True,
                     "overall": _stats(no_skill_cases),
                     "llm_path": _stats(nllm),
                     "family": _stats(ns)},
        "full_skill": {"cache_hit": True,
                       "overall": _stats(full_skill_cases),
                       "llm_path": _stats(fllm),
                       "family": _stats(fs)},
        "layered": {"cache_hit": False, "new_api_calls": arm3_calls,
                    "overall": _stats(arm3_cases),
                    "llm_path": _stats(t3llm),
                    "family": _stats(t3s)},
    }

    # --- LLM 子集：分域表（每臂） ---
    def build_llm_subset(llm_cases):
        bd = _by_domain(llm_cases, map_kp)
        src = bd.pop("_source", {})
        return bd, src

    bdm_ns, src_ns = build_llm_subset(nllm)
    bdm_fs, src_fs = build_llm_subset(fllm)
    bdm_t3, src_t3 = build_llm_subset(t3llm)

    llm_subset = {
        "overall": {
            "no_skill": _stats(nllm),
            "full_skill": _stats(fllm),
            "layered": _stats(t3llm),
        },
        "by_domain": {
            "no_skill": bdm_ns,
            "full_skill": bdm_fs,
            "layered": bdm_t3,
        },
    }

    # --- 家族控制：三臂应一致 ---
    nf = len(ns)
    all_same = (nf == len(fs) == len(t3s) and nf > 0)
    if all_same:
        for i in range(nf):
            if not (ns[i]["ok"] == fs[i]["ok"] == t3s[i]["ok"]):
                all_same = False
                break
    family_control = {
        "n": nf,
        "all_same": bool(all_same),
        "per_arm_ok": {
            "no_skill": sum(1 for c in ns if c["ok"]),
            "full_skill": sum(1 for c in fs if c["ok"]),
            "layered": sum(1 for c in t3s if c["ok"]),
        },
    }

    # --- 映射来源记录（不预设结论，仅说明口径） ---
    mapping_note = {}
    for arm_llm in (nllm, fllm, t3llm):
        for c in arm_llm:
            pid, src = map_kp(c["kp"])
            mapping_note.setdefault(c["kp"], {"pack": pid, "source": src})
    notes.append("分域映射来源：" + "; ".join(
        f"{k}→{v['pack'] or 'unmapped'}({v['source']})" for k, v in mapping_note.items()))

    # --- 摘要（≤15 行，只陈述数字与构成，不写结论） ---
    def fmt(s):
        return f"n={s['n']} ok={s['ok']} rate={s['rate']} CI={s['ci95']}"
    summary_lines = [
        "S6 三臂 A/B · 计划复用 R6/ab big40（16 LLM 路径 + 24 家族确定性路径）。",
        f"臂①无skill（cache_hit=true）：整体 {fmt(arms['no_skill']['overall'])}；",
        f"  LLM子集 {fmt(arms['no_skill']['llm_path'])}；家族 {fmt(arms['no_skill']['family'])}。",
        f"臂②全量skill（cache_hit=true）：整体 {fmt(arms['full_skill']['overall'])}；",
        f"  LLM子集 {fmt(arms['full_skill']['llm_path'])}；家族 {fmt(arms['full_skill']['family'])}。",
        f"臂③分层skill（cache_hit=false, 新增API≈{arm3_calls}次）：整体 {fmt(arms['layered']['overall'])}；",
        f"  LLM子集 {fmt(arms['layered']['llm_path'])}；家族 {fmt(arms['layered']['family'])}。",
    ]
    for dom in DOMAINS + ["unmapped"]:
        b = bdm_t3[dom]
        summary_lines.append(f"  臂③分域 {dom}：{fmt(b)}")
    summary_lines.append(
        f"家族控制：n={family_control['n']} 三臂一致 all_same={family_control['all_same']} "
        f"(no_skill={family_control['per_arm_ok']['no_skill']}, "
        f"full_skill={family_control['per_arm_ok']['full_skill']}, "
        f"layered={family_control['per_arm_ok']['layered']})。")
    summary_lines.append(
        f"预算：臂①②零新增调用；臂③新增约 {arm3_calls} 次（≤¥5）。degraded={degraded_arm3 is not None}。")
    summary = "\n".join(summary_lines)

    result = {
        "ci_method": "wilson",
        "plan": "big40 (R6/ab plan): 16 LLM-path + 24 family-deterministic",
        "arm_namespace": {
            "no_skill": "gen-green-ab:v1 (reused ab B cache)",
            "full_skill": "gen-green:v5 (reused ab A cache)",
            "layered": "gen-green-s6:v2 (fresh, v1 poisoned by pre-fix runs)",
        },
        "arms": arms,
        "llm_subset": llm_subset,
        "family_control": family_control,
        "notes": notes,
        "summary": summary,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
    }
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(summary)
    print(f"\nsaved -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
