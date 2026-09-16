"""确定性参数化模板族 · 期望（kp: prob.expectation）。

缓存命名空间：family-prob.expectation:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
离散分布律期望 E(X)=Σx_i·p_i：Rational 全程精确，归一化 Σp_i=1 可验。
判决：**matrix-extension:A**（可全构造 → 绿标家族）。

家族 disc_expect：三点分布律（x_i ∈ 1..6 互异，p1,p2 给定，p3=1-p1-p2）
  E(X) = Σ x_i·p_i。
构造即正确：期望正算与「补全分布再求和」一致；Σp=1 断言自证。
fobar_invertible：已知 E(X) 与全部 x_i、p1，反解 p2 —— 一次方程唯一。

_clean 纪律：概率分母 ≤2 位（q ≤ 99）；期望值为有理数。
题干与参数结构全部自造（抽象分布律），不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "prob.expectation"
KP_NAME = "期望与方差"
PACK_ID = "probability"
CACHE_NS = "family-prob.expectation:v1"
FAMILY = "disc_expect"

_XS = [1, 2, 3]
_P十二 = [sp.Rational(1, 4), sp.Rational(1, 2), sp.Rational(3, 4), sp.Rational(1, 6)]

# 参数格：24 组手选 (x1,x2,x3, p1,p2)——p3 = 1-p1-p2 派生（须为正）
_GRID: list[tuple[int, int, int, sp.Rational, sp.Rational]] = [
    (1, 2, 3, sp.Rational(1, 4), sp.Rational(1, 2)),
    (1, 2, 3, sp.Rational(1, 2), sp.Rational(1, 4)),
    (1, 2, 3, sp.Rational(1, 4), sp.Rational(1, 4)),
    (1, 3, 5, sp.Rational(1, 2), sp.Rational(1, 4)),
    (1, 3, 5, sp.Rational(1, 4), sp.Rational(1, 2)),
    (2, 3, 4, sp.Rational(1, 4), sp.Rational(1, 2)),
    (2, 3, 4, sp.Rational(1, 2), sp.Rational(1, 4)),
    (1, 2, 4, sp.Rational(1, 4), sp.Rational(1, 4)),
    (1, 2, 4, sp.Rational(1, 2), sp.Rational(1, 4)),
    (2, 4, 6, sp.Rational(1, 4), sp.Rational(1, 2)),
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
]
GRID: dict[str, list] = {"p5": _GRID}

_MAX_Q = 99


def _clean(p) -> bool:
    p = sp.simplify(p)
    return bool(p.is_Rational and 0 <= p <= 1 and 1 <= p.q <= _MAX_Q)


def _clean_num(x) -> bool:
    """数值干净度（期望值不是概率，无 0..1 范围约束）：有理数且分母 ≤2 位。"""
    x = sp.simplify(x)
    return bool(x.is_Rational and 1 <= x.q <= _MAX_Q)


def disc_expect(x1: int, x2: int, x3: int, p1, p2) -> dict | None:
    """三点分布律的期望 E(X)（Σp=1 归一化自证 + 期望正算）。"""
    x1, x2, x3 = int(x1), int(x2), int(x3)
    p1, p2 = sp.Rational(p1), sp.Rational(p2)
    p3 = 1 - p1 - p2
    xs = (x1, x2, x3)
    ps = (p1, p2, p3)
    if len(set(xs)) != 3 or not all(_clean(pp) and pp > 0 for pp in ps):
        return None
    e = x1 * p1 + x2 * p2 + x3 * p3

    def _fobar_unique(value) -> bool:
        """已知 E 与全部 x_i、p1，反解 p2：一次方程唯一 ∧ 落回参数格。"""
        p2_sym = sp.Symbol("p2s")
        sols = sp.solve(sp.Eq(x1 * p1 + x2 * p2_sym + x3 * (1 - p1 - p2_sym), value), p2_sym)
        hits = [s for s in sols
                if s.is_real and s.is_Rational and 0 < s < 1 - p1
                and any(sp.Rational(s) == g for g in _P十二)]
        return len(hits) == 1 and sp.Rational(hits[0]) == p2

    probs_latex = (f"$P(X={x1}) = {sp.latex(p1)}$，$P(X={x2}) = {sp.latex(p2)}$，"
                   f"$P(X={x3}) = {sp.latex(p3)}$")
    stem = (f"设离散型随机变量 $X$ 的分布律为：{probs_latex}，则 $E(X) =$ ______。")

    ans_note = (f"E(X) = {x1}×{sp.latex(p1)} + {x2}×{sp.latex(p2)} + {x3}×{sp.latex(p3)} "
                f"= {sp.latex(e)}。")
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"xs": list(xs), "p1": str(p1), "p2": str(p2)},
        "difficulty": "基础",
        "statement_md": stem, "analysis": ans_note,
        "statement_md": stem, "answer_sympy": sp.sstr(e),
        "assert": [
            # 分布合法：概率全正且归一化
            ("normalized", sp.simplify(p1 + p2 + p3 - 1) == 0 and all(pp > 0 for pp in ps)),
            # x 取值互异且为正整数（分布律形态约束）
            ("xs_valid", len(set(xs)) == 3 and all(x > 0 for x in xs)),
            # 期望仍是干净有理数
            ("clean_answer", _clean_num(e) and e > 0),
            # fobar 反解唯一
            ("fobar_invertible", _fobar_unique(e)),
        ],
    }


SPEC: dict[str, object] = {FAMILY: disc_expect}


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
