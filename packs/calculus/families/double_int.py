"""确定性参数化模板族 · 二重积分（kp: calc.double.int）。

缓存命名空间：family-calc.double.int:v1

定档依据（§5 步骤 2）：cache/sympy_matrix.json → classes.C_double_integral
判决 **A（可全构造）**，证据为「先 y 后 x / 先 x 后 y 两序积分之差 simplify 为 0」，
clean=fraction，fobar_invertible=true。本族即该判决的家族化落地。

家族 double_swap：D = {(x,y) : 0 ≤ x ≤ 1, x^p ≤ y ≤ x}（幂曲线与直线 y=x 围成），
被积函数 f = x^α·y^β，
  I = ∬_D f dσ = 1/(β+1)·[ 1/(α+β+2) − 1/(α+p(β+1)+1) ]。
构造即正确——答案由 SymPy 两种积分次序各算一遍（换序对账）+ 闭式核对三路一致，
直接对应本 kp 薄弱归因「方法选错（换序/次序选择）」。

`_clean` 纪律：任务书原文「答案分母 ≤2 位」→ q ≤ 99。
（root families.py 取 q ≤ 12，是其家族答案的更紧特例；二重积分答案天然如
1/24、2/21、1/35，故本包按 ≤2 位执行，与 sympy_matrix 的 clean=fraction 判定一致。）

题干与参数结构全部自造，不引用任何真题原文。
"""

from __future__ import annotations

import itertools

import sympy as sp

KP_ID = "calc.double.int"
KP_NAME = "二重积分"
PACK_ID = "calculus"
CACHE_NS = "family-calc.double.int:v1"
FAMILY = "double_swap"

X, Y = sp.symbols("x y", positive=True)
A_SYM = sp.symbols("A")  # fobar 反解用

# 参数格：α=x 次数，β=y 次数，p=下界幂曲线 y=x^p（4×3×2=24 格，全格合法）
GRID: dict[str, list[int]] = {"alpha": [0, 1, 2, 3], "beta": [0, 1, 2], "p": [2, 3]}

_MAX_Q = 99  # 分母 ≤2 位


def _clean(expr) -> bool:
    """答案干净度：整数或分母 ≤2 位的分数。"""
    expr = sp.nsimplify(sp.simplify(expr))
    if expr.is_Integer:
        return True
    if expr.is_Rational:
        return 1 <= expr.q <= _MAX_Q
    return False


def _closed(alpha, beta, p):
    """闭式：I = 1/(β+1)·[1/(α+β+2) − 1/(α+p(β+1)+1)]（符号 α 亦可代入，供 fobar）。"""
    alpha, beta, p = sp.sympify(alpha), sp.Integer(beta), sp.Integer(p)
    return sp.simplify(
        1 / (beta + 1) * (1 / (alpha + beta + 2) - 1 / (alpha + p * (beta + 1) + 1))
    )


def _fobar_unique(alpha: int, beta: int, p: int, value) -> bool:
    """fobar 反解断言：已知区域参数 (β,p) 与积分值，反推 α——
    solve 非空 ∧ 实根中落格者唯一 ∧ 反解等于原 α。"""
    sols = sp.solve(sp.Eq(_closed(A_SYM, beta, p), value), A_SYM)
    if not sols:
        return False
    hits = [s for s in sols if s.is_real and s.is_Integer and int(s) in GRID["alpha"]]
    return len(hits) == 1 and int(hits[0]) == alpha


def double_swap(alpha: int, beta: int, p: int) -> dict | None:
    """∬_D x^α y^β dσ，D 由 y=x^p 与 y=x 在 [0,1] 上围成（换序型二重积分）。"""
    if alpha < 0 or beta < 0 or p < 2:
        return None
    f = X**alpha * Y**beta
    # 次序一：先 y 后 x（自然序）
    i_yx = sp.simplify(sp.integrate(sp.integrate(f, (Y, X**p, X)), (X, 0, 1)))
    # 次序二：先 x 后 y（换序：x 从 y 到 y^{1/p}）
    i_xy = sp.simplify(sp.integrate(sp.integrate(f, (X, Y, Y ** sp.Rational(1, p))), (Y, 0, 1)))
    val = sp.nsimplify(i_yx)
    if not _clean(val):
        return None
    stem = (f"设平面区域 $D$ 由曲线 $y=x^{{{p}}}$ 与直线 $y=x$ 围成（$0\\le x\\le 1$），"
            f"计算二重积分 $\\displaystyle\\iint_{{D}} {sp.latex(f)}\\,\\mathrm{{d}}\\sigma=$ "
            f"______（填分数或整数）。")
    return {
        "kp": KP_NAME, "family": FAMILY, "params": {"alpha": alpha, "beta": beta, "p": p},
        "difficulty": "基础" if alpha + beta <= 1 else "进阶",
        "statement_md": stem, "answer_sympy": sp.sstr(val),
        "assert": [
            # 区域合法：(0,1) 内 x^p < x，D 非空且上下界不交叉
            ("region_valid", all(sp.simplify((t**p - t)) < 0
                                 for t in (sp.Rational(1, 4), sp.Rational(1, 2), sp.Rational(3, 4)))),
            # 换序对账：两种积分次序结果必须相等（本族的核心构造保证）
            ("order_swap_equal", sp.simplify(i_yx - i_xy) == 0),
            # 与闭式三路一致
            ("closed_form", sp.simplify(val - _closed(alpha, beta, p)) == 0),
            ("clean_answer", _clean(val)),
            ("fobar_invertible", _fobar_unique(alpha, beta, p, val)),
        ],
    }


# 模块级常量 spec（对齐 root families.py 的 _families_spec 契约）
SPEC: dict[str, object] = {FAMILY: double_swap}


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """枚举参数格，返回通过全部合法性断言的实例（确定性，无随机）。"""
    gen = SPEC[family]
    keys = list(GRID)
    out = []
    for combo in itertools.product(*(GRID[k] for k in keys)):
        if len(out) >= limit:
            break
        q = gen(**dict(zip(keys, combo)))
        if q and all(bool(v) for _, v in q["assert"]):
            out.append(q)
    return out


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    items = enumerate_family(limit=24)
    print(f"{FAMILY}: {len(items)} 合法实例 / 24 参数格")
    for it in items[:3]:
        print("  ", it["statement_md"][:70], "| ans:", it["answer_sympy"])
