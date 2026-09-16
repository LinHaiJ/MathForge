"""确定性参数化模板族 · 条件概率（kp: prob.condition）。

缓存命名空间：family-prob.condition:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
乘法公式 P(AB) = P(A)·P(B|A)：Rational 精确，条件概率定义式 P(AB)/P(A)=P(B|A)
双路径互证。判决：**matrix-extension:A**（可全构造 → 绿标家族）。

家族 cond_mult：P(A)=a，P(B|A)=w → P(AB) = a·w。
构造即正确：乘法公式正算与定义式反推双路互证。
fobar_invertible：已知 P(AB) 与 P(A) 反解 w —— 一次方程唯一（a≠0）。

_clean 纪律：概率全为有理数，分母 ≤2 位（q ≤ 99）。
题干与参数结构全部自造（抽象事件），不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "prob.condition"
KP_NAME = "条件概率"
PACK_ID = "probability"
CACHE_NS = "family-prob.condition:v1"
FAMILY = "cond_mult"

# 参数格：24 组手选 (a, w)，0<a≤1，0<w≤1
_GRID: list[tuple[sp.Rational, sp.Rational]] = [
    (sp.Rational(1, 2), sp.Rational(1, 2)), (sp.Rational(1, 2), sp.Rational(1, 4)),
    (sp.Rational(1, 2), sp.Rational(3, 4)), (sp.Rational(1, 2), sp.Rational(1, 3)),
    (sp.Rational(1, 3), sp.Rational(1, 2)), (sp.Rational(1, 3), sp.Rational(1, 3)),
    (sp.Rational(1, 3), sp.Rational(2, 3)), (sp.Rational(1, 3), sp.Rational(1, 4)),
    (sp.Rational(1, 4), sp.Rational(1, 2)), (sp.Rational(1, 4), sp.Rational(1, 4)),
    (sp.Rational(1, 4), sp.Rational(3, 4)), (sp.Rational(1, 4), sp.Rational(1, 6)),
    (sp.Rational(2, 3), sp.Rational(1, 2)), (sp.Rational(2, 3), sp.Rational(3, 4)),
    (sp.Rational(2, 3), sp.Rational(1, 4)), (sp.Rational(2, 3), sp.Rational(2, 3)),
    (sp.Rational(3, 4), sp.Rational(1, 2)), (sp.Rational(3, 4), sp.Rational(1, 3)),
    (sp.Rational(3, 4), sp.Rational(2, 3)), (sp.Rational(3, 4), sp.Rational(3, 4)),
    (sp.Rational(1, 6), sp.Rational(3, 4)), (sp.Rational(1, 6), sp.Rational(1, 2)),
    (sp.Rational(5, 6), sp.Rational(1, 2)), (sp.Rational(5, 6), sp.Rational(3, 5)),
]
GRID: dict[str, list] = {"p2": _GRID}

_MAX_Q = 99


def _clean(p) -> bool:
    p = sp.simplify(p)
    return bool(p.is_Rational and 0 <= p <= 1 and 1 <= p.q <= _MAX_Q)


def cond_mult(a, w) -> dict | None:
    """乘法公式 P(AB) = P(A)·P(B|A)（定义式反推双路互证）。"""
    a, w = sp.Rational(a), sp.Rational(w)
    if not (_clean(a) and 0 < a <= 1 and _clean(w) and 0 < w <= 1):
        return None
    joint = a * w

    def _fobar_unique(value) -> bool:
        """已知 P(AB) 与 P(A) 反解 w：solve 非空 ∧ 唯一 ∧ 落回参数格。"""
        w_sym = sp.Symbol("ws")
        sols = sp.solve(sp.Eq(a * w_sym, value), w_sym)
        hits = [s for s in sols
                if s.is_real and s.is_Rational and 0 < s <= 1
                and any(sp.Rational(s) == g for g in _PROB_W)]
        return len(hits) == 1 and sp.Rational(hits[0]) == w

    stem = (f"设事件 $A$、$B$ 满足 $P(A) = {sp.latex(a)}$，$P(B \\mid A) = {sp.latex(w)}$，"
            f"则 $P(AB) =$ ______。")

    ans_note = (f"乘法公式：P(AB) = P(A)·P(B|A) = {sp.latex(a)} × {sp.latex(w)} = {sp.latex(joint)}。")
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"a": str(a), "w": str(w)},
        "difficulty": "基础",
        "statement_md": stem, "analysis": ans_note,
        "statement_md": stem, "answer_sympy": sp.sstr(joint),
        "assert": [
            # 参数合法：0 < P(A) ≤ 1（a=0 时条件概率无定义）
            ("params_valid", 0 < a <= 1 and 0 < w <= 1),
            # 定义式反推：P(AB)/P(A) == P(B|A)
            ("definition_back", sp.simplify(joint / a - w) == 0),
            ("answer_in_range", 0 < joint <= a),
            ("clean_answer", _clean(joint)),
            ("fobar_invertible", _fobar_unique(joint)),
        ],
    }


# fobar 反解时 w 的合法取值格（与 _GRID 中的 w 集合一致）
_PROB_W = sorted({w for _, w in _GRID})

SPEC: dict[str, object] = {FAMILY: cond_mult}


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """枚举参数格，返回通过全部合法性断言的前 limit 个实例（确定性，无随机）。"""
    gen = SPEC[family]
    out = []
    for combo in GRID["p2"]:
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
