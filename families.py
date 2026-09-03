"""确定性参数化模板族（K1 修复）：ξ 由 SymPy 从题目结构推导，构造即正确。

背景（KNOWN_ISSUES K1）：让 LLM 为"求 ξ"写参数化闭式 answer_expr，它写不出随参数
变化的正确表达式，且盲解判官存在系统性偏差 → 该家族只能靠拦截链防流出。

本模块的反转：题目结构是确定的（罗尔/L-M + 二次函数），ξ 由 SymPy solve 从结构推导；
LLM 只产出题干措辞与解析（经解析实例化校验）。数学正确性=构造保证，不再依赖 LLM 数学的自觉。

三个家族：
  rolle_xi   f(x)=a(x-p)(x-q) 在 [p,q]，f(p)=f(q)=0 → f'(ξ)=0 有 ξ=(p+q)/2
  lm_xi      f(x)=ax²+bx 在 [p,p+1] → ξ = p + (a+2p+1-b)/(2a)（由 SymPy solve 推导）
  lm_rate    f(x)=ax²+bx 在 [p,p+1]，求 f'(ξ) = f(p+1)-f(p)（线性闭式，整数答案）
"""

from __future__ import annotations

import itertools
import json

import sympy as sp

X, XI = sp.symbols("x xi")


def _clean(expr) -> bool:
    """答案干净度：整数或分母 ≤2 位的分数。"""
    if expr.is_Integer:
        return True
    if expr.is_Rational:
        return 1 <= expr.q <= 12
    return False


def _compact(s: str) -> str:
    """sympy sstr 的 Matrix 输出带换行缩进，判分解析前压成单行（否则判官同值答案被判假阴性）。"""
    import re as _re
    return _re.sub(r"\s+", " ", s)


def _families_spec():
    """每个家族：参数格 + 题干模板 + 答案推导器（SymPy solve，非硬编码）。"""

    def rolle_xi(a, p, q):
        f = a * (X - p) * (X - q)
        crit = sp.solve(sp.Eq(sp.diff(f, X), 0), X)
        xi = next((s for s in crit if s.is_real and p < s < q), None)
        if xi is None or not _clean(xi):
            return None
        stem = (f"设函数 $f(x)={sp.latex(f)}$，则方程 $f'(x)=0$ 在开区间 $({sp.latex(p)},{sp.latex(q)})$ "
                f"内的解为 $\\xi=$ ______。")
        return {
            "kp": "罗尔定理", "family": "rolle_xi", "params": {"a": a, "p": p, "q": q},
            "difficulty": "基础" if a == 1 else "进阶",
            "statement_md": stem, "answer_sympy": sp.sstr(xi),
            "assert": [("xi_in_interval", p < xi < q),
                       ("fprime_zero", sp.simplify(sp.diff(f, X).subs(X, xi)) == 0)],
        }

    def lm_xi(a, b, p):
        f = a * X**2 + b * X
        rate = sp.simplify((f.subs(X, p + 1) - f.subs(X, p)) / 1)  # 区间长 1
        sols = sp.solve(sp.Eq(sp.diff(f, X).subs(X, XI), rate), XI)
        xi = next((s for s in sols if s.is_real and p < s < p + 1), None)
        if xi is None or not _clean(xi):
            return None
        stem = (f"设 $f(x)={sp.latex(f)}$ 在区间 $[{sp.latex(p)},{sp.latex(p + 1)}]$ 上满足拉格朗日中值定理条件，"
                f"则存在 $\\xi\\in({sp.latex(p)},{sp.latex(p + 1)})$ 使得 "
                f"$f'(\\xi)=\\dfrac{{f({sp.latex(p + 1)})-f({sp.latex(p)})}}{{{sp.latex(p + 1)}-{sp.latex(p)}}}$，"
                f"求 $\\xi$ 的值。")
        return {
            "kp": "拉格朗日中值定理", "family": "lm_xi", "params": {"a": a, "b": b, "p": p},
            "difficulty": "进阶",
            "statement_md": stem, "answer_sympy": sp.sstr(xi),
            "assert": [("xi_in_interval", p < xi < p + 1),
                       ("rate_eq", sp.simplify(sp.diff(f, X).subs(X, xi) - rate) == 0)],
        }

    def lm_rate(a, b, p):
        f = a * X**2 + b * X
        rate = sp.simplify(f.subs(X, p + 1) - f.subs(X, p))
        if not _clean(rate):
            return None
        sols = sp.solve(sp.Eq(sp.diff(f, X).subs(X, XI), rate), XI)
        xi = next((s for s in sols if s.is_real and p < s < p + 1), None)
        if xi is None:
            return None  # ξ 必须落在开区间内（定理合法性由结构保证）
        stem = (f"设函数 $f(x)={sp.latex(f)}$ 在区间 $[{sp.latex(p)},{sp.latex(p + 1)}]$ 上"
                f"满足拉格朗日中值定理的条件，则该定理中的 $\\xi \\in ({sp.latex(p)},{sp.latex(p + 1)})$，"
                f"则 $f'(\\xi)$ 的值为 ______（填数值）。")
        return {
            "kp": "拉格朗日中值定理", "family": "lm_rate", "params": {"a": a, "b": b, "p": p},
            "difficulty": "基础",
            "statement_md": stem, "answer_sympy": sp.sstr(rate),
            "assert": [("xi_in_interval", p < xi < p + 1),
                       ("rate_val", sp.simplify(rate - (a * (2 * p + 1) + b)) == 0)],
        }

    def det_3x3(a, b):
        A = sp.Matrix([[1, 2, a], [b, 1, 1], [0, 2, 3]])
        det = A.det()
        if det == 0 or not _clean(det):
            return None
        stem = (f"计算三阶行列式 $\\begin{{vmatrix}}{sp.latex(A[0, 0])} & {sp.latex(A[0, 1])} & {sp.latex(A[0, 2])}\\\\"
                f"{sp.latex(A[1, 0])} & {sp.latex(A[1, 1])} & {sp.latex(A[1, 2])}\\\\"
                f"{sp.latex(A[2, 0])} & {sp.latex(A[2, 1])} & {sp.latex(A[2, 2])}\\end{{vmatrix}}=$ ______（填数值）。")
        return {
            "kp": "行列式", "family": "det_3x3", "params": {"a": a, "b": b},
            "difficulty": "综合",
            "statement_md": stem, "answer_sympy": _compact(sp.sstr(det)),
            "assert": [("det_expand", sp.simplify(A.det() - det) == 0)],
        }

    def matmul_entry(a, b, i, j):
        A = sp.Matrix([[1, 2], [3, a]])
        B = sp.Matrix([[2, 1], [b, 3]])
        C = A * B
        val = C[i - 1, j - 1]
        if not _clean(val):
            return None
        stem = (f"设 $A={sp.latex(A)}$，$B={sp.latex(B)}$，若 $C=AB$，"
                f"则 $c_{{{i}{j}}}=$ ______（填数值）。")
        return {
            "kp": "矩阵乘法", "family": "matmul_entry", "params": {"a": a, "b": b, "i": i, "j": j},
            "difficulty": "基础",
            "statement_md": stem, "answer_sympy": _compact(sp.sstr(val)),
            "assert": [("entry_eq", sp.simplify(C[i - 1, j - 1] - val) == 0)],
        }

    def inverse_2x2(a, b, c, d):
        A = sp.Matrix([[a, b], [c, d]])
        if A.det() == 0:
            return None
        inv = A.inv()
        if not all(_clean(e) for e in inv):
            return None
        stem = (f"求矩阵 $A={sp.latex(A)}$ 的逆矩阵 $A^{{-1}}$。"
                f"作答格式：Matrix([[第1行],[第2行]])，如 Matrix([[1,2],[3,4]])。")
        return {
            "kp": "逆矩阵", "family": "inverse_2x2", "params": {"a": a, "b": b, "c": c, "d": d},
            "difficulty": "进阶",
            "statement_md": stem, "answer_sympy": _compact(sp.sstr(inv)),
            "assert": [("inv_roundtrip", sp.simplify(A * inv - sp.eye(2)) == sp.zeros(2, 2))],
        }

    return {"rolle_xi": rolle_xi, "lm_xi": lm_xi, "lm_rate": lm_rate,
            "matmul_entry": matmul_entry, "inverse_2x2": inverse_2x2, "det_3x3": det_3x3}


def enumerate_family(family: str, limit: int = 6) -> list[dict]:
    """枚举参数格，返回通过合法性断言的实例（确定性，无随机）。"""
    gen = _families_spec()[family]
    grids = {
        "rolle_xi": ({"a": [1, 2, 3], "p": [-2, 1], "q": [2, 4]}),
        "lm_xi": ({"a": [1, 2, 3], "b": [-2, 1, 2], "p": [0, 1]}),
        "lm_rate": ({"a": [1, 2, 3], "b": [-1, 1, 2], "p": [0, 1]}),
        "matmul_entry": {"a": [1, 2, 3], "b": [1, 2, 3], "i": [1, 2], "j": [1, 2]},
        "inverse_2x2": {"a": [1, 2, 3], "b": [0, 1, 2], "c": [1, 2], "d": [1, 2, 3]},
        "det_3x3": {"a": [1, 2], "b": [1, 2, 3]},
    }
    keys = list(grids[family])
    out = []
    for combo in itertools.product(*(grids[family][k] for k in keys)):
        if len(out) >= limit:
            break
        q = gen(**dict(zip(keys, combo)))
        if q and all(bool(v) for _, v in q["assert"]):
            out.append(q)
    return out


if __name__ == "__main__":
    for fam in _families_spec():
        items = enumerate_family(fam, limit=3)
        print(f"{fam}: {len(items)} 合法实例")
        for it in items[:2]:
            print("  ", it["statement_md"][:80], "| ans:", it["answer_sympy"])
