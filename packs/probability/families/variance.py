"""确定性参数化模板族 · 方差（kp: prob.variance）。

缓存命名空间：family-prob.variance:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
Var(X) = E[X²] − E(X)²：Rational 全程精确；
对偶路径 D(X) = Σ(x_i − E)²·p_i 恒等（方差计算公式 vs 定义式）。
判决：**matrix-extension:A**（可全构造 → 绿标家族）。

家族 disc_var：与 disc_expect 同构的三点分布律
  E(X) = Σx_i·p_i，Var(X) = E[X²] − E(X)²。
构造即正确：计算公式（E[X²]−E²）与定义式（Σ(x_i−E)²p_i）双路互证。
fobar_invertible：已知 Var 与全部 x_i、p1，反解 p2 —— 方程唯一 ∧ 落回参数格。

_clean 纪律：概率分母 ≤2 位；方差为非负有理数，分母 ≤2 位。
题干与参数结构全部自造（抽象分布律），不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "prob.variance"
KP_NAME = "方差"
PACK_ID = "probability"
CACHE_NS = "family-prob.variance:v1"
FAMILY = "disc_var"

# 参数格：24 组手选 (x1,x2,x3, p1,p2)——x 非等距与等距混合，p3 = 1-p1-p2 派生
_GRID: list[tuple[int, int, int, sp.Rational, sp.Rational]] = [
    (1, 2, 3, sp.Rational(1, 4), sp.Rational(1, 2)),
    (1, 2, 3, sp.Rational(1, 2), sp.Rational(1, 4)),
    (1, 2, 3, sp.Rational(1, 4), sp.Rational(1, 4)),
    (1, 3, 5, sp.Rational(1, 2), sp.Rational(1, 4)),
    (1, 3, 5, sp.Rational(1, 4), sp.Rational(1, 2)),
    (2, 3, 4, sp.Rational(1, 4), sp.Rational(1, 2)),
    (2, 4, 6, sp.Rational(1, 4), sp.Rational(1, 4)),
    (1, 2, 4, sp.Rational(1, 2), sp.Rational(1, 4)),
    (2, 4, 6, sp.Rational(1, 2), sp.Rational(1, 4)),
    (1, 4, 5, sp.Rational(1, 4), sp.Rational(1, 2)),
    (3, 4, 5, sp.Rational(1, 4), sp.Rational(1, 4)),
    (1, 5, 6, sp.Rational(1, 2), sp.Rational(1, 4)),
    (2, 3, 6, sp.Rational(1, 4), sp.Rational(1, 4)),
    (2, 5, 6, sp.Rational(1, 4), sp.Rational(1, 2)),
    (1, 2, 6, sp.Rational(1, 2), sp.Rational(1, 4)),
    (3, 5, 6, sp.Rational(1, 4), sp.Rational(1, 2)),
    (1, 3, 4, sp.Rational(1, 4), sp.Rational(1, 4)),
    (2, 4, 5, sp.Rational(1, 2), sp.Rational(1, 4)),
    (1, 4, 6, sp.Rational(1, 4), sp.Rational(1, 4)),
    (2, 3, 5, sp.Rational(1, 2), sp.Rational(1, 4)),
    (3, 4, 6, sp.Rational(1, 4), sp.Rational(1, 2)),
    (1, 5, 6, sp.Rational(1, 4), sp.Rational(1, 4)),
    (1, 2, 5, sp.Rational(1, 4), sp.Rational(1, 2)),
    (4, 5, 6, sp.Rational(1, 4), sp.Rational(1, 4)),
]
GRID: dict[str, list] = {"p5": _GRID}

_MAX_Q = 99


def _clean_prob(p) -> bool:
    p = sp.simplify(p)
    return bool(p.is_Rational and 0 <= p <= 1 and 1 <= p.q <= _MAX_Q)


def _clean_num(x) -> bool:
    x = sp.simplify(x)
    return bool(x.is_Rational and 1 <= x.q <= _MAX_Q)


def disc_var(x1: int, x2: int, x3: int, p1, p2) -> dict | None:
    """三点分布律的方差 Var(X)（计算公式×定义式双路互证）。"""
    x1, x2, x3 = int(x1), int(x2), int(x3)
    p1, p2 = sp.Rational(p1), sp.Rational(p2)
    p3 = 1 - p1 - p2
    xs, ps = (x1, x2, x3), (p1, p2, p3)
    if len(set(xs)) != 3 or not all(_clean_prob(pp) and pp > 0 for pp in ps):
        return None
    e = x1 * p1 + x2 * p2 + x3 * p3
    e2 = x1**2 * p1 + x2**2 * p2 + x3**2 * p3
    var_formula = e2 - e**2                                  # 计算公式
    var_def = (x1 - e) ** 2 * p1 + (x2 - e) ** 2 * p2 + (x3 - e) ** 2 * p3  # 定义式
    var = sp.simplify(var_formula)

    def _fobar_unique(value) -> bool:
        """已知 Var 与全部 x_i、p1，反解 p2：方程唯一 ∧ 落回参数格。"""
        p2_sym = sp.Symbol("p2s")
        p3_sym = 1 - p1 - p2_sym
        e_s = x1 * p1 + x2 * p2_sym + x3 * p3_sym
        eq = sp.Eq((x1**2 * p1 + x2**2 * p2_sym + x3**2 * p3_sym) - e_s**2, value)
        sols = [s for s in sp.solve(eq, p2_sym)
                if s.is_real and s.is_Rational and 0 < s < 1 - p1]
        hits = [s for s in sols if any(sp.Rational(s) == g for g in
                                       [sp.Rational(1, 4), sp.Rational(1, 2), sp.Rational(1, 6), sp.Rational(3, 4)])]
        return len(hits) == 1 and sp.Rational(hits[0]) == p2

    probs_latex = (f"$P(X={x1}) = {sp.latex(p1)}$，$P(X={x2}) = {sp.latex(p2)}$，"
                   f"$P(X={x3}) = {sp.latex(p3)}$")
    stem = (f"设离散型随机变量 $X$ 的分布律为：{probs_latex}，则 $D(X) =$ ______。")

    ans_note = (f"E(X) = {sp.latex(e)}，E(X²) = {sp.latex(e2)}，"
                f"D(X) = E(X²) − E(X)² = {sp.latex(var)}；等价地 Σ(xᵢ−E)²·pᵢ 同值。")
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"xs": list(xs), "p1": str(p1), "p2": str(p2)},
        "difficulty": "基础" if x3 - x1 <= 2 else "进阶",
        "statement_md": stem, "analysis": ans_note,
        "statement_md": stem, "answer_sympy": sp.sstr(var),
        "assert": [
            # 分布合法：概率全正且归一化
            ("normalized", sp.simplify(p1 + p2 + p3 - 1) == 0 and all(pp > 0 for pp in ps)),
            # 双路恒等：计算公式 == 定义式
            ("dual_identity", sp.simplify(var_formula - var_def) == 0),
            # 方差非负且干净
            ("nonnegative_clean", var >= 0 and _clean_num(var)),
            # fobar 反解唯一
            ("fobar_invertible", _fobar_unique(var)),
        ],
    }


SPEC: dict[str, object] = {FAMILY: disc_var}


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """枚举参数格，返回通过全部合法性断言的前 limit 个实例（确定性，无随机）。"""
    gen = SPEC[family]
    out = []
    for combo in GRID["p5"]:
        if len(out) >= limit:
            break
        q = gen(*combo)
        if q and all(bool(v) for _, v in q["assert"]):
            out.append(q)
    return out


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    items = enumerate_family(limit=24)
    print(f"{FAMILY}: {len(items)} 合法实例 / {len(_GRID)} 参数格")
    for it in items[:3]:
        print("  ", it["statement_md"][:100], "| ans:", it["answer_sympy"])
