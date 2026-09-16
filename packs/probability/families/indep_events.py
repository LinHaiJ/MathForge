"""确定性参数化模板族 · 多事件独立性（kp: prob.independence）。

缓存命名空间：family-prob.independence:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
K12 弱区「三事件加法公式」→ 相互独立三事件：
  P(A∪B∪C) = 1-(1-a)(1-b)(1-c) = a+b+c-ab-ac-bc+abc（容斥恒等式两路一致），
  SymPy Rational 全程精确。判决：**matrix-extension:A**。
K12 弱区修复（KNOWN_ISSUES K12：多事件概率的 LLM 参数化闭式不可靠区）。

家族 indep3_add：A,B,C 相互独立，P(A)=a, P(B)=b, P(C)=c（互异正有理数）。
  问法唯一：P(A∪B∪C) = ______（三事件加法公式/独立性）。
构造即正确：答案由「补集积 1-(1-a)(1-b)(1-c)」与「容斥展开」两路推导互证。
fobar_invertible：已知答案与 a、b，反解 c —— 一次方程 solve 非空 ∧ 唯一 ∧ 落回参数格。

_clean 纪律：概率全为有理数，分母 ≤2 位（q ≤ 99）。
题干与参数结构全部自造（抽象事件 A/B/C，无真题语境），不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "prob.independence"
KP_NAME = "事件独立性"
PACK_ID = "probability"
CACHE_NS = "family-prob.independence:v1"
FAMILY = "indep3_add"

# 参数格：24 组手选 (a, b, c)——互异有理概率（分母 ≤6），正负覆盖不需要（概率恒正）
_PROBS = [sp.Rational(1, 2), sp.Rational(1, 3), sp.Rational(1, 4),
          sp.Rational(1, 6), sp.Rational(2, 3), sp.Rational(3, 4)]
_GRID: list[tuple[sp.Rational, sp.Rational, sp.Rational]] = [
    (sp.Rational(1, 2), sp.Rational(1, 3), sp.Rational(1, 4)),
    (sp.Rational(1, 2), sp.Rational(1, 3), sp.Rational(1, 6)),
    (sp.Rational(1, 2), sp.Rational(1, 4), sp.Rational(1, 6)),
    (sp.Rational(1, 3), sp.Rational(1, 4), sp.Rational(1, 6)),
    (sp.Rational(1, 2), sp.Rational(2, 3), sp.Rational(1, 3)),
    (sp.Rational(1, 2), sp.Rational(1, 3), sp.Rational(3, 4)),
    (sp.Rational(1, 4), sp.Rational(1, 3), sp.Rational(2, 3)),
    (sp.Rational(1, 6), sp.Rational(1, 3), sp.Rational(1, 2)),
    (sp.Rational(2, 3), sp.Rational(3, 4), sp.Rational(1, 2)),
    (sp.Rational(1, 3), sp.Rational(1, 6), sp.Rational(3, 4)),
    (sp.Rational(1, 2), sp.Rational(3, 4), sp.Rational(1, 4)),
    (sp.Rational(1, 4), sp.Rational(2, 3), sp.Rational(3, 4)),
    (sp.Rational(1, 6), sp.Rational(1, 4), sp.Rational(1, 2)),
    (sp.Rational(2, 3), sp.Rational(1, 3), sp.Rational(1, 4)),
    (sp.Rational(1, 2), sp.Rational(1, 6), sp.Rational(1, 4)),
    (sp.Rational(1, 3), sp.Rational(3, 4), sp.Rational(1, 2)),
    (sp.Rational(3, 4), sp.Rational(1, 4), sp.Rational(1, 3)),
    (sp.Rational(1, 6), sp.Rational(2, 3), sp.Rational(3, 4)),
    (sp.Rational(1, 2), sp.Rational(2, 3), sp.Rational(3, 4)),
    (sp.Rational(1, 3), sp.Rational(1, 4), sp.Rational(1, 2)),
    (sp.Rational(1, 4), sp.Rational(1, 6), sp.Rational(2, 3)),
    (sp.Rational(2, 3), sp.Rational(1, 2), sp.Rational(1, 4)),
    (sp.Rational(1, 6), sp.Rational(1, 2), sp.Rational(2, 3)),
    (sp.Rational(3, 4), sp.Rational(1, 6), sp.Rational(1, 3)),
]
GRID: dict[str, list] = {"p3": _GRID}

_MAX_Q = 99


def _clean(p) -> bool:
    p = sp.simplify(p)
    return bool(p.is_Rational and 0 < p and 1 <= p.q <= _MAX_Q)


def indep3_add(a, b, c) -> dict | None:
    """相互独立三事件的并概率（加法公式/补集积双路互证）。"""
    a, b, c = sp.Rational(a), sp.Rational(b), sp.Rational(c)
    if not all(_clean(x) and x < 1 for x in (a, b, c)):
        return None
    if len({a, b, c}) != 3:
        return None  # 互异：保证 fobar 反解唯一
    via_complement = 1 - (1 - a) * (1 - b) * (1 - c)
    via_inclusion = (a + b + c) - (a * b + a * c + b * c) + a * b * c
    answer = sp.simplify(via_complement)

    def _fobar_unique(value) -> bool:
        """已知答案与 a、b，反解 c：solve 非空 ∧ 唯一 ∧ 落回参数格。"""
        c_sym = sp.Symbol("cs")
        eq = sp.Eq(1 - (1 - a) * (1 - b) * (1 - c_sym), value)
        sols = sp.solve(eq, c_sym)
        hits = [s for s in sols
                if s.is_real and s.is_Rational and 1 <= sp.Rational(s).q <= _MAX_Q
                and any(sp.Rational(s) == g for g in _PROBS)]
        return len(hits) == 1 and sp.Rational(hits[0]) == c

    stem = (f"设事件 $A$、$B$、$C$ 相互独立，且 $P(A) = {sp.latex(a)}$, "
            f"$P(B) = {sp.latex(b)}$, $P(C) = {sp.latex(c)}$，"
            f"则 $P(A \\cup B \\cup C) =$ ______。")

    ans_note = (f"相互独立时 P(A∪B∪C) = 1 − (1−{sp.latex(a)})(1−{sp.latex(b)})(1−{sp.latex(c)}) "
                f"= {sp.latex(answer)}；等价的加法公式：a+b+c−ab−ac−bc+abc 同值。")
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"a": str(a), "b": str(b), "c": str(c)},
        "difficulty": "基础" if max(a, b, c) <= sp.Rational(1, 2) and min(a, b, c) >= sp.Rational(1, 4) else "进阶",
        "statement_md": stem, "analysis": ans_note,
        "statement_md": stem, "answer_sympy": sp.sstr(answer),
        "assert": [
            # 概率合法：0 < P < 1
            ("probs_valid", all(0 < x < 1 for x in (a, b, c))),
            # 两路恒等：补集积 == 容斥展开（加法公式自证核心）
            ("inclusion_exclusion", sp.simplify(via_complement - via_inclusion) == 0),
            # 结果仍是合法概率
            ("answer_in_range", 0 < answer < 1),
            ("clean_answer", _clean(answer)),
            ("fobar_invertible", _fobar_unique(answer)),
        ],
    }


SPEC: dict[str, object] = {FAMILY: indep3_add}


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
