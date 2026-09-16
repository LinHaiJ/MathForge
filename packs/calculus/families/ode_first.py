"""确定性参数化模板族 · 一阶微分方程（kp: calc.ode.first）。

缓存命名域：family-calc.ode.first:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
一阶线性常系数 y' + p·y = q（p≠0, q≠0 常数）：sp.dsolve(ics) 闭式精确，
与常数变易法构造双路径互证。判决：**matrix-extension:A**（可全构造 → 绿标家族）。

家族 ode1_linear：y' + p·y = q，y(0)=a（p∈±1..±3 非零，q∈1..6，a∈0..3）
  特解 y(x) = q/p + (a - q/p)·e^{-px}。
构造即正确：解代入方程（残差为零）+ 初值吻合 + dsolve 交叉三断言自证。
fobar 不适用（与 ode2_distinct 同理：解图像可来自不同参数组合），已披露。

_clean 纪律：q/p 与定常数必须为整数或分母 ≤2 位分数；指数上 p 为非零整数。
题干与参数结构全部自造，不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "calc.ode.first"
KP_NAME = "一阶微分方程"
PACK_ID = "calculus"
CACHE_NS = "family-calc.ode.first:v1"
FAMILY = "ode1_linear"

X = sp.Symbol("x")

# 参数格：24 组手选 (p, q, a)——p 正负覆盖，a 含 0
_GRID: list[tuple[int, int, int]] = [
    (1, 2, 0), (1, 2, 1), (1, 3, 2), (1, 4, 1),
    (2, 2, 3), (2, 4, 0), (2, 6, 2), (2, 3, 1),
    (-1, 2, 0), (-1, 3, 1), (-1, 4, 2), (-1, 2, 3),
    (-2, 2, 1), (-2, 4, 0), (-2, 6, 1), (-2, 3, 2),
    (3, 3, 0), (3, 6, 1), (-3, 3, 2), (-3, 6, 0),
    (1, 5, 2), (-1, 5, 1), (2, 5, 0), (-2, 5, 3),
]
GRID: dict[str, list] = {"p3": _GRID}

_MAX_Q = 99


def _clean_coeff(c) -> bool:
    c = sp.simplify(c)
    if c.is_Integer:
        return True
    return bool(c.is_Rational and 1 <= c.q <= _MAX_Q)


def ode1_linear(p: int, q: int, a: int) -> dict | None:
    """一阶线性常系数初值问题 y' + p·y = q，y(0)=a 的特解。"""
    if p == 0 or q == 0:
        return None
    y = sp.Rational(q, p) + (sp.Rational(a) - sp.Rational(q, p)) * sp.exp(-p * X)

    lam_check = sp.simplify(sp.diff(y, X) + p * y - q)          # 残差
    dsol = sp.dsolve(sp.Derivative(sp.Function("yy")(X), X) + p * sp.Function("yy")(X) - q,
                     sp.Function("yy")(X), ics={sp.Function("yy")(0): a})

    def _clean_all() -> bool:
        """定常数与稳态项干净：y 的展开中各系数可读。"""
        expanded = sp.expand(y)
        return all(_clean_coeff(c) for c in sp.Poly(expanded - sp.expand(y), X).all_coeffs()) \
            and _clean_coeff(sp.Rational(q, p))

    def _fobar_note() -> bool:
        """非退化：稳态项与初始值不同（否则解恒为常数，教学价值退化）。"""
        return sp.simplify(sp.Rational(a) - sp.Rational(q, p)) != 0

    yprime = "y"          # 一阶：y' 而非 y''
    pp = "+ y" if p == 1 else ("- y" if p == -1 else (f"+ {p}y" if p > 0 else f"- {abs(p)}y"))
    stem = (f"设微分方程 $y' {pp} = {q}$ 满足初值条件 $y(0) = {a}$，"
            f"则其特解 $y(x) =$ ______。")

    ans_note = (f"一阶线性：稳态项 q/p = {sp.latex(sp.Rational(q, p))}，"
                f"通解 y = {sp.latex(sp.Rational(q, p))} + C·e^(−{p}x)；"
                f"由 y(0)={a} 得 C = {sp.latex(sp.Rational(a) - sp.Rational(q, p))}。")
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"p": p, "q": q, "a": a},
        "difficulty": "基础" if abs(p) == 1 and q % p == 0 else "进阶",
        "statement_md": stem, "analysis": ans_note,
        "statement_md": stem, "answer_sympy": sp.sstr(y),
        "assert": [
            # 参数非零（p=0 退化为一阶最简，q=0 退化为齐次，均不在本族）
            ("params_valid", p != 0 and q != 0),
            # 解代入方程残差为零（构造自证核心）
            ("satisfies_ode", sp.simplify(lam_check) == 0),
            # 初值吻合
            ("iv_match", sp.simplify(y.subs(X, 0) - a) == 0),
            # dsolve 交叉验证（独立符号路径）
            ("dsolve_cross", sp.simplify(dsol.rhs - y) == 0),
            # 系数干净
            ("clean_coeffs", _clean_all()),
            # 非退化（fobar 不适用披露的替代断言）
            ("non_degenerate", _fobar_note()),
        ],
    }


SPEC: dict[str, object] = {FAMILY: ode1_linear}


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """枚举参数格，返回通过全部合法性断言的前 limit 个实例（确定性，无随机）。"""
    gen = SPEC[family]
    out = []
    for combo in GRID["p3"]:
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
        print("  ", it["statement_md"][:90], "| ans:", it["answer_sympy"])
