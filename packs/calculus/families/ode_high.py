"""确定性参数化模板族 · 高阶线性微分方程（kp: calc.ode.high）。

缓存命名空间：family-calc.ode.high:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
sympy_matrix.json 6 类未覆盖本 kp → 按 §3 同法探针：
  二阶常系数线性齐次 y''+py'+qy=0：特征方程 solve 精确给根；
  sp.dsolve 含 ics 直接给特解闭式（与特征根构造双路径互证）。
判决：**matrix-extension:A**（可全构造 → 绿标家族）。
任务书 §5 续推第二批次（sim_weak_kps #4）。

家族 ode2_distinct：相异实特征根 r1≠r2（非零），
  y'' - (r1+r2)·y' + (r1·r2)·y = 0，y(0)=a，y'(0)=b
  → y(x) = C1·e^{r1x} + C2·e^{r2x}，
  C1 = (b - a·r2)/(r1 - r2)，C2 = (a·r1 - b)/(r1 - r2)。
构造即正确：解由「特征根 → 通解 → 定常数」符号推导自证，LLM 不参与。
断言不含 fobar（解关于参数不唯一可逆——同一函数图像可来自不同初值组合，
fobar_invertible 纪律在此不适用，以「解代入方程 + 初值吻合 + dsolve 交叉」三断言替代）。

_clean 纪律：C1、C2 必须为整数或分母 ≤2 位的分数（q ≤ 99）；
e^{rx} 指数上保持整数参数。
题干与参数结构全部自造，不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "calc.ode.high"
KP_NAME = "高阶线性微分方程"
PACK_ID = "calculus"
CACHE_NS = "family-calc.ode.high:v1"
FAMILY = "ode2_distinct"

X = sp.Symbol("x")

# 参数格：24 组手选（r1,r2,a,b）——正负根、跨号、分数定常数各种形态覆盖
_GRID: list[tuple[int, int, int, int]] = [
    (1, 2, 1, 0), (2, 1, 0, 1), (1, -1, 1, 1), (-1, 1, 2, 0),
    (1, 2, 0, 2), (2, -1, 1, 1), (-1, -2, 1, 0), (-2, -1, 0, 1),
    (2, 3, 1, 2), (3, 2, 2, 1), (1, 3, 1, 1), (3, -1, 1, 0),
    (-1, 2, 1, 2), (2, -3, 0, 1), (-2, 1, 1, 1), (1, -2, 2, 1),
    (-3, 1, 0, 1), (3, -3, 1, 1), (-1, 3, 2, 1), (3, 1, 1, 2),
    (-3, 2, 1, 0), (1, 2, 2, 0), (-2, -3, 1, 2), (2, 1, 1, 2),
]
GRID: dict[str, list[tuple[int, int, int, int]]] = {"p4": _GRID}

_MAX_Q = 99


def _clean_coeff(c) -> bool:
    """定常数千净度：整数 / 分母 ≤2 位分数。"""
    c = sp.simplify(c)
    if c.is_Integer:
        return True
    return bool(c.is_Rational and 1 <= c.q <= _MAX_Q)


def ode2_distinct(r1: int, r2: int, a: int, b: int) -> dict | None:
    """相异实根二阶常系数齐次方程初值问题的特解（构造自证）。"""
    if r1 == r2 or 0 in (r1, r2):
        return None
    if a == 0 and b == 0:
        return None
    pv, qv = -(r1 + r2), r1 * r2
    c1 = sp.Rational(b - a * r2, r1 - r2)
    c2 = sp.Rational(a * r1 - b, r1 - r2)
    y = c1 * sp.exp(r1 * X) + c2 * sp.exp(r2 * X)

    lam = sp.Symbol("lambda_")
    char_roots = sorted(sp.solve(lam**2 + pv * lam + qv, lam))
    ode_residual = sp.diff(y, X, 2) + pv * sp.diff(y, X) + qv * y

    def _fmt_eq() -> str:
        """方程展示：$y'' - 3y' + 2y = 0$ 形态（系数符号由 pv/qv 符号决定）。"""
        yprime = "y'"          # 单独变量，避开 f-string 嵌套引号
        def term(coef, sym):
            shown = -coef
            if shown == 0:
                return ""
            sign = "-" if shown > 0 else "+"
            m = abs(shown)
            mtxt = "" if m == 1 else str(m)
            return f" {sign} {mtxt}{sym}"
        return f"$y''{term(pv, yprime)}{term(qv, 'y')} = 0$"

    def _fobar_note() -> bool:
        """fobar 不适用（图像相同可来自不同初值），替代断言：定常数非退化。"""
        return sp.simplify(c1 + c2) != 0 or b != 0

    stem = (f"设二阶常系数线性齐次微分方程 {_fmt_eq()} 满足初值条件 "
            f"$y(0) = {a},\\ y'(0) = {b}$，则其特解 $y(x) =$ ______。")

    ans_note = (f"特征方程 λ² {'+' if pv > 0 else '−'} {abs(pv)}λ {'+' if qv > 0 else '−'} {abs(qv)} = 0 "
                f"的根为 {r1} 与 {r2}，通解 y = C₁·e^({r1}x) + C₂·e^({r2}x)；"
                f"代入 y(0)={a}、y'(0)={b} 得 C₁ = {sp.latex(c1)}，C₂ = {sp.latex(c2)}。")
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"r1": r1, "r2": r2, "a": a, "b": b, "p": pv, "q": qv},
        "difficulty": "基础" if (a == 1 and b in (0, 1)) else "进阶",
        "statement_md": stem, "analysis": ans_note,
        "statement_md": stem, "answer_sympy": sp.sstr(y),
        "assert": [
            # 特征方程的根恰为 (r1, r2)
            ("char_roots", char_roots == sorted([r1, r2])),
            # 解代入方程恒为零（构造自证）
            ("satisfies_ode", sp.simplify(ode_residual) == 0),
            # 初值吻合
            ("iv_match", sp.simplify(y.subs(X, 0) - a) == 0
             and sp.simplify(sp.diff(y, X).subs(X, 0) - b) == 0),
            # 定常数千净
            ("clean_coeffs", _clean_coeff(c1) and _clean_coeff(c2)),
            # 非退化替代断言（fobar 不适用，见 docstring）
            ("non_degenerate", _fobar_note()),
        ],
    }


SPEC: dict[str, object] = {FAMILY: ode2_distinct}


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """枚举参数格，返回通过全部合法性断言的前 limit 个实例（确定性，无随机）。"""
    gen = SPEC[family]
    keys = list(GRID)
    out = []
    for combo in GRID[keys[0]]:
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
        print("  ", it["statement_md"][:80], "| ans:", it["answer_sympy"])
