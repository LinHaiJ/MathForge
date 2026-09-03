"""语料体检 + 冷启动难度打分（路径一「选题 + 诊断」的地基）。

解决两件事：
1. **大纲覆盖度体检**：按数一/数二/数三分别对照考纲模块，给出每个模块的题量与缺口评级。
   ——背景：cxy_full 语料概率统计仅占 5%（数一大纲权重 22%），三重积分几乎为 0。
2. **难度冷启动**：语料难度字段 100% 缺失。先用特征代理分（零 API 成本）打底，
   再用 `calibrate` 子命令让本人对抽样题打分，最小二乘拟合权重，把代理分校准成个人难度分。

诚实口径：
- 大纲**只规定科目级权重**（数一 高数56/线代22/概率22），**模块级权重为本项目估计**，
  报告中一律标注 [估计]，不得当作官方口径引用。
- 代理分**未经校准时仅为排序启发式**，不是标定难度。校准后才是个人难度分。

用法：
    python scripts/corpus_triage.py report --exam math1
    python scripts/corpus_triage.py report --exam math3 --out cache/bank_enriched.jsonl
    python scripts/corpus_triage.py calibrate --n 30        # 抽样打分，拟合权重
    python scripts/corpus_triage.py report --exam math1 --weights cache/diff_weights.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent.parent
CORPUS = HERE / "cache" / "cxy_full.jsonl"

# ── 考纲模块映射 ────────────────────────────────────────────────
# 字段：(模块名, 模块权重[估计], 匹配关键词)
# 科目级权重为官方口径；模块级权重是本项目按真题分值结构的估计，标注 [估计]。

CALC = [
    ("函数极限连续", 0.08, ["极限", "连续"]),
    ("一元微分学", 0.08, ["一元微分"]),
    ("一元积分学", 0.08, ["一元积分"]),
    ("向量代数与空间解析几何", 0.03, ["空间解析几何", "向量代数", "平面方程", "直线方程", "曲面", "柱面", "方向余弦"]),
    ("多元微分学", 0.07, ["多元微分"]),
    ("多元积分学", 0.10, ["二重积分", "三重积分", "曲线积分", "曲面积分", "格林", "高斯公式", "斯托克斯"]),
    ("无穷级数", 0.06, ["级数"]),
    ("常微分方程", 0.06, ["微分方程"]),
]
LINEAR = [
    ("行列式", 0.03, ["行列式"]),
    ("矩阵", 0.05, ["矩阵"]),
    ("向量", 0.03, ["向量"]),
    ("线性方程组", 0.04, ["线性方程组"]),
    ("特征值与特征向量", 0.04, ["特征值"]),
    ("二次型", 0.03, ["二次型"]),
]
PROB = [
    ("随机事件与概率", 0.04, ["事件与概率", "条件概率", "全概率", "贝叶斯"]),
    ("一维随机变量", 0.04, ["一维随机变量", "随机变量及其分布"]),
    ("多维随机变量", 0.04, ["二维随机变量", "多维随机变量"]),
    ("数字特征", 0.03, ["数字特征", "期望", "方差", "协方差"]),
    ("大数定律与中心极限定理", 0.02, ["大数定律", "中心极限定理"]),
    ("数理统计与参数估计", 0.03, ["统计初步", "参数估计", "矩估计", "极大似然", "区间估计"]),
    ("假设检验", 0.02, ["假设检验", "显著性"]),
]

EXAMS = {
    # 数一：全考
    "math1": {"name": "考研数学一", "blocks": [("高等数学", 0.56, CALC),
                                               ("线性代数", 0.22, LINEAR),
                                               ("概率统计", 0.22, PROB)]},
    # 数二：不考概率统计、无穷级数、空间解析几何、三重/曲线/曲面积分
    "math2": {"name": "考研数学二", "blocks": [("高等数学", 0.78, [m for m in CALC if m[0] not in
                                                              ("向量代数与空间解析几何", "无穷级数")]),
                                               ("线性代数", 0.22, LINEAR)]},
    # 数三：不考空间解析几何、三重/曲线/曲面积分、傅里叶；概率权重更高
    "math3": {"name": "考研数学三", "blocks": [("高等数学", 0.56, [m for m in CALC if m[0] not in
                                                              ("向量代数与空间解析几何",)]),
                                               ("线性代数", 0.22, LINEAR),
                                               ("概率统计", 0.22, PROB)]},
}

# ── 难度代理特征 ────────────────────────────────────────────────

PARAM_RE = re.compile(r"(?<![A-Za-z])([abkKmnNABCMR]|lambda|alpha|theta|a_|b_)(?![A-Za-z])")
LATEX_RE = re.compile(r"\\[a-zA-Z]+")
MULTI_RE = re.compile(r"[（(]\s*[12１２]\s*[)）]|[①②③]|\b[（(]\s*[ⅠⅡ]\s*[)）]")
GOAL_W = {"证明": 1.00, "讨论": 0.85, "判断": 0.60, "计算": 0.50, "求": 0.45, "设": 0.30}


def features(r: dict) -> dict:
    """冷启动难度代理特征，全部 0-1 归一（长度/密度用分位在 report 里统一做）。"""
    stem = r.get("stem") or ""
    cat = r.get("category_full_path") or ""
    n = max(len(stem), 1)
    goal = next((v for k, v in GOAL_W.items() if k in stem[:120]), 0.30)
    return {
        "len": len(stem),                                    # 后处理做分位归一
        "density": min(len(LATEX_RE.findall(stem)) / n * 40, 1.0),
        "is_zhenti": 1.0 if cat.startswith("历年真题") else 0.0,
        "depth": min(len([x for x in cat.split("/") if x.strip()]) / 5.0, 1.0),
        "goal": goal,
        "has_param": 1.0 if PARAM_RE.search(stem) else 0.0,
        "multi_part": 1.0 if MULTI_RE.search(stem) else 0.0,
    }


FKEYS = ["density", "is_zhenti", "depth", "goal", "has_param", "multi_part"]
DEFAULT_W = {"density": 0.15, "is_zhenti": 0.25, "depth": 0.10,
             "goal": 0.25, "has_param": 0.15, "multi_part": 0.10}


def score_all(rows: list[dict], weights: dict) -> list[float]:
    feats = [features(r) for r in rows]
    lens = np.array([f["len"] for f in feats], dtype=float)
    pct = (lens.argsort().argsort() / max(len(lens) - 1, 1))  # 长度分位 0-1
    out = []
    for i, f in enumerate(feats):
        s = pct[i] * 0.20
        for k in FKEYS:
            s += weights.get(k, DEFAULT_W[k]) * f[k]
        out.append(s)
    lo, hi = min(out), max(out)
    return [round(100 * (s - lo) / max(hi - lo, 1e-9)) for s in out]


# ── 覆盖度 ─────────────────────────────────────────────────────

def match_module(r: dict, keywords: list[str]) -> bool:
    cat = r.get("category_full_path") or ""
    stem = r.get("stem") or ""
    return any(k in cat or k in stem for k in keywords)


def coverage(rows: list[dict], exam: str) -> list[dict]:
    spec = EXAMS[exam]
    out = []
    for subj, sweight, mods in spec["blocks"]:
        mw_sum = sum(m[1] for m in mods) or 1.0
        for name, mw, kws in mods:
            hit = [r for r in rows if match_module(r, kws)]
            share = mw / mw_sum * sweight          # 归一化到科目权重
            out.append({"subject": subj, "module": name, "n": len(hit),
                        "weight_est": round(share, 4), "keywords": kws})
    return out


def grade(n: int, weight: float, total: int) -> str:
    """缺口评级：按"若按权重分配，应有题量 vs 实际题量"的比值。"""
    expect = max(total * weight, 1)
    ratio = n / expect
    if ratio >= 0.8:
        return "充足"
    if ratio >= 0.4:
        return "偏薄"
    if ratio >= 0.15:
        return "不足"
    return "严重缺口"


# ── 子命令 ─────────────────────────────────────────────────────

def cmd_report(args) -> None:
    rows = [json.loads(l) for l in CORPUS.open(encoding="utf-8") if l.strip()]
    spec = EXAMS[args.exam]
    cov = coverage(rows, args.exam)
    total = sum(c["n"] for c in cov) or 1

    weights = DEFAULT_W
    if args.weights and Path(args.weights).exists():
        weights = json.loads(Path(args.weights).read_text(encoding="utf-8"))
        print(f"[校准权重已加载] {args.weights}")
    else:
        print("[未校准] 使用默认启发式权重，难度分仅供排序，非标定难度")

    scores = score_all(rows, weights)

    print(f"\n{'='*72}\n{spec['name']} · 语料体检（语料 {len(rows)} 题）\n{'='*72}")
    print(f"{'科目':<8}{'模块':<22}{'题数':>6}{'权重[估计]':>10}{'缺口评级':>10}")
    print("-" * 72)
    for c in cov:
        g = grade(c["n"], c["weight_est"], total)
        flag = " <<<" if g in ("不足", "严重缺口") else ""
        print(f"{c['subject']:<8}{c['module']:<22}{c['n']:>6}{c['weight_est']*100:>9.1f}%{g:>10}{flag}")

    gaps = [c for c in cov if grade(c["n"], c["weight_est"], total) in ("不足", "严重缺口")]
    print("-" * 72)
    print(f"缺口模块 {len(gaps)}/{len(cov)}")
    for c in sorted(gaps, key=lambda x: x["n"])[:8]:
        print(f"   {c['subject']}/{c['module']}：{c['n']} 题（权重 {c['weight_est']*100:.1f}%）")

    # 难度分档
    bands = [(0, 25, "基础"), (25, 50, "进阶"), (50, 75, "综合"), (75, 101, "冲刺")]
    print("\n难度分档（代理分，未校准仅供排序）")
    for lo, hi, name in bands:
        n = sum(1 for s in scores if lo <= s < hi)
        print(f"   {name:<6} [{lo:>3},{hi:>3})  {n:>5} 题  {100*n/len(scores):>5.1f}%")

    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        with outp.open("w", encoding="utf-8") as f:
            for r, s in zip(rows, scores):
                r2 = dict(r)
                r2["difficulty_proxy"] = s
                r2["calibrated"] = bool(args.weights and Path(args.weights).exists())
                f.write(json.dumps(r2, ensure_ascii=False) + "\n")
        print(f"\n已写出：{outp}（含 difficulty_proxy 字段）")


def cmd_calibrate(args) -> None:
    rows = [json.loads(l) for l in CORPUS.open(encoding="utf-8") if l.strip()]
    rng = random.Random(args.seed)
    idx = rng.sample(range(len(rows)), min(args.n, len(rows)))
    samp = [rows[i] for i in idx]

    print(f"抽样 {len(samp)} 题，请按你自己的感觉给每题打 0-100 的难度分。")
    print("（0 = 看一眼就会，100 = 完全没思路。直接回车跳过该题。）\n")

    feats, ys = [], []
    for k, r in enumerate(samp, 1):
        stem = (r.get("stem") or "")[:150].replace("\n", " ")
        print(f"[{k}/{len(samp)}] {stem}")
        try:
            raw = input("  难度 0-100 > ").strip()
        except EOFError:
            break
        if not raw:
            continue
        try:
            y = float(raw)
        except ValueError:
            continue
        if not 0 <= y <= 100:
            continue
        f = features(r)
        f["len_pct"] = None  # 长度分位在全量上算，此处占位
        feats.append(f)
        ys.append(y)

    if len(ys) < 8:
        print(f"\n有效样本仅 {len(ys)} 条，少于 8 条不拟合（会过拟合）。请下次多打几题。")
        return

    lens = np.array([f["len"] for f in feats], dtype=float)
    pct = lens.argsort().argsort() / max(len(lens) - 1, 1)
    X = np.column_stack([pct] + [[f[k] for f in feats] for k in FKEYS])
    X = np.column_stack([np.ones(len(X)), X])          # 截距项
    y = np.array(ys, dtype=float)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ coef
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    names = ["intercept", "len_pct"] + FKEYS
    w = {n: round(float(c), 4) for n, c in zip(names, coef)}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"weights": w, "n_samples": len(ys), "r2": round(r2, 3)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n拟合完成：n={len(ys)}  R²={r2:.3f}（样本小，R² 仅供参考）")
    for n in names:
        print(f"   {n:<12} {w[n]:>8.4f}")
    print(f"\n权重已写出：{out}")
    if r2 < 0.3:
        print("⚠️ R² 偏低：特征解释力不足，或样本量太小。建议扩到 40+ 题再校准。")


def main() -> None:
    ap = argparse.ArgumentParser(description="语料体检 + 冷启动难度打分")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("report", help="大纲覆盖度体检 + 难度代理分")
    p1.add_argument("--exam", choices=list(EXAMS), default="math1")
    p1.add_argument("--out", help="输出富化后的 jsonl 路径")
    p1.add_argument("--weights", help="校准权重 json（calibrate 产出）")
    p1.set_defaults(func=cmd_report)

    p2 = sub.add_parser("calibrate", help="抽样人工打分，拟合难度权重")
    p2.add_argument("--n", type=int, default=30)
    p2.add_argument("--seed", type=int, default=7)
    p2.add_argument("--out", default=str(HERE / "cache" / "diff_weights.json"))
    p2.set_defaults(func=cmd_calibrate)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
