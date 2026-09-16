"""确定性参数化模板族 · 几何概型（kp: prob.geometric）。

缓存命名域：family-prob.geometric:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
单位正方形 (0,1)² 均匀投点，区域 {(x,y): x+y ≤ c}（0<c≤1）：
P = ∫₀^c (c-t) dt = c²/2 —— Rational 精确，积分与解析式双路径。
判决：**matrix-extension:A**（可全构造 → 绿标家族）。

家族 geo_square_line：向单位正方形均匀投点，求 x+y ≤ c 的概率（c 为有理数，0<c≤1）。
  答案 P = c²/2。
构造即正确：面积分正算与解析式 c²/2 双路互证。
fobar_invertible：已知概率反解 c —— solve 唯一正根 ∧ 落回参数格。

_clean 纪律：c 与概率全为有理数，分母 ≤2 位（q ≤ 99）。
题干与参数结构全部自造，不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "prob.geometric"
KP_NAME = "几何概型"
PACK_ID = "probability"
CACHE_NS = "family-prob.geometric:v1"
FAMILY = "geo_square_line"

# 参数格：24 组手选 c（0<c≤1 有理数，分母 ≤6，含 1 边界与各种真分数）
_GRID: list[sp.Rational] = [
    sp.Rational(1, 2), sp.Rational(1, 3), sp.Rational(2, 3), sp.Rational(1, 4),
    sp.Rational(3, 4), sp.Rational(1, 5), sp.Rational(2, 5), sp.Rational(3, 5),
    sp.Rational(4, 5), sp.Rational(1, 6), sp.Rational(5, 6), sp.Rational(1),
    sp.Rational(2, 6), sp.Rational(3, 6), sp.Rational(4, 6), sp.Rational(5, 6),
    sp.Rational(1, 3), sp.Rational(2, 4), sp.Rational(1, 2), sp.Rational(2, 2),
    sp.Rational(1, 4), sp.Rational(3, 6), sp.Rational(2, 5), sp.Rational(1),
]
GRID: dict[str, list] = {"c": _GRID}

_MAX_Q = 99


def _clean(x) -> bool:
    x = sp.simplify(x)
    return bool(x.is_Rational and 0 < x and 1 <= x.q <= _MAX_Q)


def geo_square_line(c) -> dict | None:
    """单位正方形投点，P(x+y ≤ c) = c²/2（0<c≤1）。"""
    c = sp.Rational(c)
    if not (_clean(c) and 0 < c <= 1):
        return None
    y = sp.Symbol("y")
    area_integral = sp.integrate(c - sp.Symbol("t"), (sp.Symbol("t"), 0, c))
    closed = c**2 / 2
    answer = sp.simplify(closed)

    def _fobar_unique(value) -> bool:
        """已知概率反解 c：唯一正根 ∧ 落回参数格。"""
        c_sym = sp.Symbol("cs", positive=True)
        sols = sp.solve(sp.Eq(c_sym**2 / 2, value), c_sym)
        hits = [s for s in sols
                if s.is_real and s.is_Rational and 0 < s <= 1
                and any(sp.Rational(s) == g for g in _GRID)]
        return len(hits) == 1 and sp.Rational(hits[0]) == c

    stem = (f"在单位正方形 $\\{{(x, y) \\mid 0 < x < 1,\\ 0 < y < 1\\}}$ 内均匀投点，"
            f"则点 $(x, y)$ 满足 $x + y \\le {sp.latex(c)}$ 的概率为 ______。")

    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"c": str(c)},
        "difficulty": "基础" if c in (sp.Rational(1, 2), sp.Rational(1)) else "进阶",
        "statement_md": stem,
        "analysis": (f"几何概型：满足 x+y≤{sp.latex(c)} 的区域是直线 x+y={sp.latex(c)} "
                     f"下方的三角形，面积 = {sp.latex(c)}²/2 = {sp.latex(answer)}；"
                     f"单位正方形总面积为 1，故概率 = 面积比 = {sp.latex(answer)}。"),
        "answer_sympy": sp.sstr(answer),
        "assert": [
            # 参数合法：0 < c ≤ 1（超界需分段面积，本族不覆盖）
            ("c_in_range", 0 < c <= 1),
            # 面积分正算 == 解析式 c²/2（双路互证）
            ("area_integral", sp.simplify(area_integral - closed) == 0),
            ("answer_in_range", 0 < answer <= sp.Rational(1, 2)),
            ("clean_answer", _clean(answer)),
            ("fobar_invertible", _fobar_unique(answer)),
        ],
    }


SPEC: dict[str, object] = {FAMILY: geo_square_line}


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """枚举参数格，返回通过全部合法性断言的前 limit 个实例（确定性，无随机）。"""
    gen = SPEC[family]
    out = []
    for c in GRID["c"]:
        if len(out) >= limit:
            break
        q = gen(c)
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
