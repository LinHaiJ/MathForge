"""确定性参数化模板族 · 导数概念与几何意义（kp: calc.derivative.def）。

缓存命名空间：family-calc.derivative.def:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
三次多项式 f(x) = k·x³ 在 x=c 处的切线：f'(c) = 3k·c² 精确，
切线方程 y = f'(c)(x−c) + f(c) 与「点斜式 + 导数几何意义」双路径互证。
判决：**matrix-extension:A**（可全构造 → 绿标家族）。

家族 tangent_cubic：f(x) = k·x³（k 为非零有理数），求 x = c 处的切线方程。
  答案 y = 3k·c²·x − 2k·c³（符号表达式，SymPy 等价判分）。
构造即正确：斜率 = f'(c) 与切点在曲线上两断言自证。
fobar_invertible：已知切线斜率与 c，反解 k —— 一次方程唯一。

_clean 纪律：k = 有理数（分母 ≤2 位），c = 非零整数。
题干与参数结构全部自造，不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "calc.derivative.def"
KP_NAME = "导数概念与几何意义"
PACK_ID = "calculus"
CACHE_NS = "family-calc.derivative.def:v1"
FAMILY = "tangent_cubic"

X = sp.Symbol("x")

# 参数格：24 组手选 (k_num, k_den, c)——k = k_num/k_den，正负覆盖
_GRID: list[tuple[int, int, int]] = [
    (1, 1, 1), (1, 1, 2), (1, 1, -1), (1, 1, -2),
    (2, 1, 1), (2, 1, -1), (1, 2, 1), (1, 2, -1),
    (1, 2, 2), (1, 2, -2), (3, 1, 1), (3, 1, -2),
    (1, 3, 1), (1, 3, -1), (1, 3, 2), (1, 3, -2),
    (1, 4, 1), (1, 4, -1), (5, 1, 1), (5, 1, -1),
    (3, 2, 1), (3, 2, -1), (1, 5, 2), (1, 5, -2),
]
GRID: dict[str, list] = {"p3": _GRID}

_MAX_Q = 99


def tangent_cubic(k_num: int, k_den: int, c: int) -> dict | None:
    """f(x) = k·x³ 在 x=c 处的切线方程（点斜式构造自证）。"""
    k = sp.Rational(k_num, k_den)
    if c == 0:
        return None
    if not (k != 0 and 1 <= k.q <= _MAX_Q):
        return None
    f = k * X**3
    slope = sp.diff(f, X).subs(X, c)              # f'(c) = 3k c²
    yc = f.subs(X, c)                             # f(c) = k c³
    tangent = slope * (X - c) + yc                # 点斜式
    slope_indep = 3 * k * c**2                    # 独立路径：导数公式直算

    def _fobar_unique(value_slope) -> bool:
        """已知切线斜率与 c，反解 k：一次方程唯一 ∧ 落回参数格。"""
        k_sym = sp.Rational(1)
        sols = sp.solve(sp.Eq(3 * sp.Symbol("ks") * c**2, value_slope), sp.Symbol("ks"))
        hits = [s for s in sols
                if s.is_real and s.is_Rational and 1 <= sp.Rational(s).q <= _MAX_Q
                and any(sp.Rational(s) == sp.Rational(g[0], g[1]) for g in _GRID)]
        return len(hits) == 1 and sp.Rational(hits[0]) == k

    k_latex = "" if k == 1 else sp.latex(k)
    stem = (f"设曲线 $f(x) = {k_latex}x^3$，则该曲线在点 $x = {c}$ 处的切线方程为 "
            f"$y =$ ______。")

    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"k_num": k_num, "k_den": k_den, "c": c},
        "difficulty": "基础" if abs(c) == 1 else "进阶",
        "statement_md": stem,
        "analysis": (f"导数的几何意义：切线斜率 = f'({c}) = 3·{sp.latex(k)}·{c}² = {sp.latex(slope)}；"
                     f"切点在曲线上：f({c}) = {sp.latex(yc)}。"
                     f"点斜式：y = {sp.latex(slope)}(x − {c}) + {sp.latex(yc)}，"
                     f"整理得 y = {sp.latex(sp.expand(tangent))}。"),
        "answer_sympy": sp.sstr(sp.expand(tangent)),
        "assert": [
            # 切点在曲线上（几何自证）
            ("point_on_curve", sp.simplify(yc - k * c**3) == 0),
            # 斜率 = 导数（导数定义/幂函数求导两路一致）
            ("slope_is_derivative", sp.simplify(slope - slope_indep) == 0),
            # 切线在切点处的值 == f(c)（切线过切点）
            ("tangent_passes_point", sp.simplify(tangent.subs(X, c) - yc) == 0),
            # 答案形态：x 一次式且斜率干净
            ("clean_answer", sp.simplify(tangent - (slope * X + (yc - slope * c))) == 0
             and _clean_slope(slope)),
            ("fobar_invertible", _fobar_unique(slope)),
        ],
    }


def _clean_slope(slope) -> bool:
    slope = sp.simplify(slope)
    return bool(slope.is_Rational and slope != 0 and 1 <= slope.q <= _MAX_Q)


SPEC: dict[str, object] = {FAMILY: tangent_cubic}


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
        print("  ", it["statement_md"][:80], "| ans:", it["answer_sympy"])
