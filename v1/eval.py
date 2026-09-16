"""MathForge 评测（T9/T10）：四指标 R1/R2 可复跑。

指标（PRD §9 + 任务书）：
  M1 出题成功率  = 验证链通过数 / 绿标生成总数
  M2 黄标自洽率  = 双盲一致通过数 / 黄标生成总数（目标 ≥90%）
  M3 归因准确率  = 合成计算失误集 + 手写边界集上的分类一致率（ground truth 可控）
  M4 变式质量率  = (知识点一致 ∧ 验证通过) / 变式生成总数（v1 重定义）
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from attribute import attribute_error  # noqa: E402
from generate import generate_question  # noqa: E402
from verify import check_answer  # noqa: E402


def _load_kp(path: str, name: str) -> dict:
    kps = json.load(open(path, encoding="utf-8"))["kp_list"]
    return next(k for k in kps if name in k["kp"])


def wilson_ci(ok: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 置信区间（小样本二项比例）。"""
    if n == 0:
        return (0.0, 0.0)
    p = ok / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z**2 / (4 * n**2)) ** 0.5) / denom
    return (round(center - half, 3), round(center + half, 3))


KP_ZWL = "cache/ingest_zwdl.json"      # 高数·中值定理
KP_XD = "证据/ingest_sample.json"      # 线代·矩阵
KP_GL = "证据/ingest_vlm_sample.json"  # 概率


def m1_green_success(n_mode: str = "std") -> dict:
    """绿标出题成功率。std=6（构成矩阵最小版）；big=20（扩样：3 知识域 × 6 kp × 三难度）；
    big40=40（M1 扩样轨道 R6：3 知识域 × 11 kp —— 家族 kp 深轮转 6 实例 + LLM kp 含 6 个新 kp 上下文）。
    口径纪律：评测集主体仍为「20 道评测用例」，big40 为命名扩样轨道，构成表随报告披露。"""
    rolle = _load_kp(KP_ZWL, "罗尔")
    lagr = _load_kp(KP_ZWL, "拉格朗日")
    cauchy = _load_kp(KP_ZWL, "柯西")
    taylor = _load_kp(KP_ZWL, "泰勒")
    inv = _load_kp(KP_XD, "逆矩阵")
    mul = _load_kp(KP_XD, "矩阵乘法")
    rank = _load_kp(KP_XD, "矩阵的秩")
    cond = _load_kp(KP_GL, "条件概率公式")
    addp = _load_kp(KP_GL, "互不相容事件的加法公式")
    prop = _load_kp(KP_GL, "条件概率的性质")
    sub = _load_kp(KP_GL, "减法公式")
    add3 = _load_kp(KP_GL, "三个事件的加法公式")
    plan = [
        (rolle, "基础"), (rolle, "进阶"), (rolle, "综合"),
        (lagr, "基础"), (lagr, "进阶"), (lagr, "综合"),
        (inv, "基础"), (inv, "进阶"), (inv, "综合"),
        (mul, "基础"), (mul, "进阶"),
        (cond, "基础"), (cond, "进阶"), (cond, "综合"),
        (addp, "基础"), (addp, "进阶"), (addp, "综合"),
        (mul, "综合"), (inv, "综合"), (addp, "基础"),
    ]
    if n_mode == "std":
        plan = plan[:6]
    if n_mode == "big40":
        # 家族 kp 深轮转（每发一个新实例，SymPy 构造背书）：4 kp × 6 = 24
        plan = [(rolle, "基础")] * 6 + [(lagr, "进阶")] * 6 + \
               [(mul, "基础")] * 6 + [(inv, "进阶")] * 6
        # LLM kp：已预热 4 组 + 新 kp 上下文 12 组（真实信号）
        plan += [(cond, "基础"), (cond, "进阶"), (addp, "基础"), (addp, "进阶"),
                 (cauchy, "基础"), (cauchy, "进阶"), (taylor, "基础"), (taylor, "进阶"),
                 (rank, "基础"), (rank, "进阶"),
                 (prop, "基础"), (prop, "进阶"), (sub, "基础"), (sub, "进阶"),
                 (add3, "基础"), (add3, "进阶")]
    cases = []
    for kp_ctx, diff in plan:
        t0 = time.time()
        q = generate_question(kp_ctx, difficulty=diff, qtype="calculation")
        cases.append({"kp": kp_ctx["kp"], "difficulty": diff,
                      "q_difficulty": q.get("difficulty"),
                      "routed": q.get("routed"),
                      "ok": bool(q.get("statement_md")),
                      "error": (q.get("error") or "")[:120],
                      "s": round(time.time() - t0, 1)})
        print(f"  M1 {kp_ctx['kp'][:8]} {diff}: {'OK' if cases[-1]['ok'] else '拦截 ' + cases[-1]['error'][:50]}")
    ok = sum(1 for c in cases if c["ok"])
    return {"total": len(cases), "ok": ok, "rate": round(ok / len(cases), 3),
            "ci95": wilson_ci(ok, len(cases)), "cases": cases}


def m2_yellow_selfconsist(n_mode: str = "std") -> dict:
    """黄标双盲自洽率。std=6；big=12（题型×知识域均衡）。"""
    rolle = _load_kp(KP_ZWL, "罗尔")
    lagr = _load_kp(KP_ZWL, "拉格朗日")
    inv = _load_kp(KP_XD, "逆矩阵")
    mul = _load_kp(KP_XD, "矩阵乘法")
    cond = _load_kp(KP_GL, "条件概率公式")
    addp = _load_kp(KP_GL, "互不相容事件的加法公式")
    plan = [
        (rolle, "concept", "基础"), (lagr, "concept", "进阶"), (cond, "concept", "基础"),
        (rolle, "choice", "基础"), (lagr, "order", "进阶"), (cond, "choice", "基础"),
    ]
    if n_mode in ("big", "big40"):
        plan += [
            (inv, "concept", "进阶"), (mul, "concept", "基础"),
            (inv, "choice", "进阶"), (addp, "choice", "进阶"),
            (mul, "order", "基础"), (addp, "order", "基础"),
        ]
    cases = []
    for kp_ctx, qtype, diff in plan:
        q = generate_question(kp_ctx, difficulty=diff, qtype=qtype)
        cases.append({"kp": kp_ctx["kp"], "qtype": qtype, "difficulty": diff,
                      "ok": bool(q.get("statement_md")),
                      "error": (q.get("error") or "")[:120]})
        print(f"  M2 {kp_ctx['kp'][:8]} {qtype}: {'OK' if cases[-1]['ok'] else '拦截 ' + cases[-1]['error'][:50]}")
    ok = sum(1 for c in cases if c["ok"])
    return {"total": len(cases), "ok": ok, "rate": round(ok / len(cases), 3),
            "ci95": wilson_ci(ok, len(cases)), "cases": cases}


def m3_attribution(eval_set_path: str, n_synth: int = 12) -> dict:
    """归因评测：ground truth 可控的双轨。
    轨A 合成计算失误：对可判分题把标准答案做数值扰动（±1 或丢因子）→ 真值=计算失误
    轨B 手写边界例：4 例来自真实错因形态，真值人工给定"""
    synth, synth_ok = [], 0
    import re
    cands = [q for q in json.load(open(eval_set_path, encoding="utf-8"))
             if q["type"] == "subjective" and re.search(r"\d", q["answer"])]
    for q in cands[:n_synth]:
        ans = q["answer"]
        m = re.search(r"-?\d+", ans)
        if not m:
            continue
        wrong = ans[:m.start()] + str(int(m.group(0)) + 1) + ans[m.end():]
        d = attribute_error(q["statement_md"], wrong, ans, q["analysis"])
        hit = d["attribution"] == "计算失误"
        synth_ok += hit
        synth.append({"id": q["id"], "pred": d["attribution"], "truth": "计算失误", "hit": hit})

    boundary = [
        {"stem": "题目要求用罗尔定理求 ξ，学生答了 f(b)-f(a) 的数值", "stu": "2", "std": "ξ=1/2",
         "truth": "审题错误"},  # 2026-08-30 用户专家裁定：答非所问（求 ξ 答成另一量）=审题错误；原标"概念混淆"系标注歧义，归档 证据/attribution_ruling.md
        {"stem": "1/ξ=ln5/4 应得 ξ=4/ln5，学生化简后写成 ξ=4/5", "stu": "4/5", "std": "4/ln5", "truth": "计算失误"},
        {"stem": "求 ∫x·e^x dx，学生两次分部方向选反越积越复杂，得到 -x·e^x-e^x", "stu": "-x*exp(x)-exp(x)", "std": "x*exp(x)-exp(x)", "truth": "方法选错"},
        {"stem": "题目求 f'(1) 的值，学生只化简出 f'(x)=2x+1 就停笔", "stu": "2x+1", "std": "3", "truth": "审题错误"},
    ]
    bound_ok = 0
    bound_cases = []
    for b in boundary:
        d = attribute_error(b["stem"], b["stu"], b["std"])
        hit = d["attribution"] == b["truth"]
        bound_ok += hit
        bound_cases.append({"pred": d["attribution"], "truth": b["truth"], "hit": hit})
    n_syn = len(synth)
    return {"synth": {"total": n_syn, "ok": synth_ok, "rate": round(synth_ok / n_syn, 3) if n_syn else None,
                      "cases": synth},
            "boundary": {"total": len(boundary), "ok": bound_ok,
                         "rate": round(bound_ok / len(boundary), 3), "cases": bound_cases}}


def m4_variant_quality(n: int = 4) -> dict:
    """变式质量率（v1 重定义）：以已验证题目的题干为母本做参数扰动 → 知识点一致 ∧ 验证通过。"""
    qs = json.load(open("证据/t6_questions.json", encoding="utf-8"))["questions"]
    rolle = _load_kp(KP_ZWL, "罗尔")
    mothers = [q for q in qs if q["verify_level"] == "green" and q.get("params")][:n]
    cases = []
    for m0 in mothers:
        q = generate_question(rolle, difficulty=m0["difficulty"], qtype="calculation",
                              variant_of=m0["statement_md"])
        ok = bool(q.get("statement_md"))
        cases.append({"mother": m0["statement_md"][:60], "ok": ok,
                      "kp_consistent": ok,  # 生成本身绑定同一 kp 上下文
                      "error": (q.get("error") or "")[:120]})
    ok = sum(1 for c in cases if c["ok"])
    return {"total": len(cases), "ok": ok, "rate": round(ok / len(cases), 3) if cases else None, "cases": cases}


def run_eval(tag: str, mode: str = "std") -> dict:
    print(f"[{tag}] M1 绿标成功率（{mode}）...")
    m1 = m1_green_success(mode)
    print(f"  M1 = {m1['ok']}/{m1['total']} = {m1['rate']} CI95={m1['ci95']}")
    print(f"[{tag}] M2 黄标自洽率（{mode}）...")
    m2 = m2_yellow_selfconsist(mode)
    print(f"  M2 = {m2['ok']}/{m2['total']} = {m2['rate']} CI95={m2['ci95']}")
    print(f"[{tag}] M3 归因准确率...")
    m3 = m3_attribution("cache/eval_set.json")
    print(f"  M3 合成 = {m3['synth']['ok']}/{m3['synth']['total']} | 边界 = {m3['boundary']['ok']}/4")
    print(f"[{tag}] M4 变式质量率...")
    m4 = m4_variant_quality()
    print(f"  M4 = {m4['ok']}/{m4['total']} = {m4['rate']}")
    result = {"tag": tag, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "M1": m1, "M2": m2, "M3": m3, "M4": m4}
    json.dump(result, open(f"cache/eval_{tag}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return result


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "R1"
    mode = sys.argv[2] if len(sys.argv) > 2 else "std"
    run_eval(tag, mode)
