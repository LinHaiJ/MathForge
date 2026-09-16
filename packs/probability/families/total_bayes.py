"""确定性参数化模板族 · 全概率与贝叶斯公式（kp: prob.total_bayes）。

缓存命名空间：family-prob.total_bayes:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
全概率公式 P(A) = P(B1)P(A|B1) + P(B2)P(A|B2)（B1B2 互斥完备）：
Rational 全程精确，补集对偶 1-P(¬A) 双路径互证。判决：**matrix-extension:A**。
本族为「贝叶斯后验」的前置（先有 P(A) 才有后验；后验问法下一批）。

家族 bayes2_total：B1,B2 互斥完备，P(B1)=p，P(A|B1)=q1，P(A|B2)=q2（q1≠q2）
  P(A) = p·q1 + (1-p)·q2。
构造即正确：正算（全概率）与对偶（1-P(¬A)）两路互证，LLM 不参与。
fobar_invertible：已知 P(A) 与 q1、q2 反解 p —— 一次方程 solve 非空 ∧ 唯一 ∧ 落回参数格。

_clean 纪律：概率全为有理数，分母 ≤2 位（q ≤ 99）。
题干与参数结构全部自造（抽象事件），不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "prob.total_bayes"
KP_NAME = "全概率与贝叶斯公式"
PACK_ID = "probability"
CACHE_NS = "family-prob.total_bayes:v1"
FAMILY = "bayes2_total"

_PROBS = [sp.Rational(1, 2), sp.Rational(1, 3), sp.Rational(1, 4), sp.Rational(2, 3)]
_CONDS = [sp.Rational(1, 4), sp.Rational(1, 2), sp.Rational(3, 4)]

# 参数格：24 组手选 (p, q1, q2)，q1≠q2（fobar 反解唯一的前提）
_GRID: list[tuple[sp.Rational, sp.Rational, sp.Rational]] = [
    (sp.Rational(1, 2), sp.Rational(1, 4), sp.Rational(3, 4)),
    (sp.Rational(1, 2), sp.Rational(3, 4), sp.Rational(1, 4)),
    (sp.Rational(1, 3), sp.Rational(1, 4), sp.Rational(1, 2)),
    (sp.Rational(1, 3), sp.Rational(1, 2), sp.Rational(1, 4)),
    (sp.Rational(1, 4), sp.Rational(1, 2), sp.Rational(3, 4)),
    (sp.Rational(1, 4), sp.Rational(3, 4), sp.Rational(1, 2)),
    (sp.Rational(2, 3), sp.Rational(1, 4), sp.Rational(1, 2)),
    (sp.Rational(2, 3), sp.Rational(1, 2), sp.Rational(1, 4)),
    (sp.Rational(1, 2), sp.Rational(1, 4), sp.Rational(1, 2)),
    (sp.Rational(1, 2), sp.Rational(1, 2), sp.Rational(1, 4)),
    (sp.Rational(1, 3), sp.Rational(1, 4), sp.Rational(3, 4)),
    (sp.Rational(1, 3), sp.Rational(3, 4), sp.Rational(1, 2)),
    (sp.Rational(1, 4), sp.Rational(1, 4), sp.Rational(1, 2)),
    (sp.Rational(1, 4), sp.Rational(1, 2), sp.Rational(1, 4)),
    (sp.Rational(2, 3), sp.Rational(3, 4), sp.Rational(1, 2)),
    (sp.Rational(2, 3), sp.Rational(1, 2), sp.Rational(3, 4)),
    (sp.Rational(1, 2), sp.Rational(3, 4), sp.Rational(1, 2)),
    (sp.Rational(1, 3), sp.Rational(1, 2), sp.Rational(3, 4)),
    (sp.Rational(1, 4), sp.Rational(3, 4), sp.Rational(1, 4)),
    (sp.Rational(2, 3), sp.Rational(1, 4), sp.Rational(3, 4)),
    (sp.Rational(1, 3), sp.Rational(3, 4), sp.Rational(1, 4)),
    (sp.Rational(1, 4), sp.Rational(1, 4), sp.Rational(3, 4)),
    (sp.Rational(1, 2), sp.Rational(1, 4), sp.Rational(3, 4)),
    (sp.Rational(2, 3), sp.Rational(3, 4), sp.Rational(1, 4)),
]
GRID: dict[str, list] = {"p3": _GRID}

_MAX_Q = 99


def _clean(p) -> bool:
    p = sp.simplify(p)
    return bool(p.is_Rational and 0 <= p <= 1 and 1 <= p.q <= _MAX_Q)


def bayes2_total(p, q1, q2) -> dict | None:
    """互斥完备事件组下的全概率 P(A)（正算×对偶双路互证）。"""
    p, q1, q2 = sp.Rational(p), sp.Rational(q1), sp.Rational(q2)
    if not (_clean(p) and 0 < p < 1):
        return None
    if q1 == q2:
        return None  # 反解唯一性前提
    if not (_clean(q1) and _clean(q2)):
        return None

    via_law = p * q1 + (1 - p) * q2                       # 全概率公式
    via_dual = 1 - (p * (1 - q1) + (1 - p) * (1 - q2))    # 对偶：1 - P(¬A)
    answer = sp.simplify(via_law)

    def _fobar_unique(value) -> bool:
        """已知 P(A) 与 q1、q2 反解 p：一次方程，solve 非空 ∧ 唯一 ∧ 落回参数格。"""
        p_sym = sp.Symbol("ps")
        sols = sp.solve(sp.Eq(p_sym * q1 + (1 - p_sym) * q2, value), p_sym)
        hits = [s for s in sols
                if s.is_real and s.is_Rational and 0 < s < 1
                and any(sp.Rational(s) == g for g in _PROBS)]
        return len(hits) == 1 and sp.Rational(hits[0]) == p

    stem = (f"设 $B_1$、$B_2$ 构成互斥完备事件组，$P(B_1) = {sp.latex(p)}$，"
            f"$P(A \\mid B_1) = {sp.latex(q1)}$，$P(A \\mid B_2) = {sp.latex(q2)}$，"
            f"则 $P(A) =$ ______。")

    ans_note = (f"全概率公式：P(A) = P(B₁)·P(A|B₁) + P(B₂)·P(A|B₂) "
                f"= {sp.latex(p * q1)} + {sp.latex((1 - p) * q2)} = {sp.latex(answer)}。")
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"p": str(p), "q1": str(q1), "q2": str(q2)},
        "difficulty": "基础" if q1 in (sp.Rational(1, 2),) or q2 in (sp.Rational(1, 2),) else "进阶",
        "statement_md": stem, "analysis": ans_note,
        "statement_md": stem, "answer_sympy": sp.sstr(answer),
        "assert": [
            # 参数合法
            ("params_valid", 0 < p < 1 and 0 <= q1 <= 1 and 0 <= q2 <= 1 and q1 != q2),
            # 两路恒等：全概率公式 == 1 - P(¬A)
            ("dual_identity", sp.simplify(via_law - via_dual) == 0),
            ("answer_in_range", 0 < answer < 1),
            ("clean_answer", _clean(answer)),
            ("fobar_invertible", _fobar_unique(answer)),
        ],
    }


SPEC: dict[str, object] = {FAMILY: bayes2_total}


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
