"""确定性参数化模板族 · 古典概型（kp: prob.classical）。

缓存命名空间：family-prob.classical:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
超几何抽次品 P(恰好 m 件次品) = C(k,m)·C(N-k,n-m)/C(N,n)：
sp.binomial Rational 精确；分母 C(N,n) 在 N≤8 时 ≤ 56 ≤ 2 位。
判决：**matrix-extension:A**（可全构造 → 绿标家族）。

家族 hypergeo：N 件产品含 k 件次品，任取 n 件，恰有 m 件次品的概率。
构造即正确：分子分母由 sp.binomial 组合计算，另以「逐件连乘（无条件概率递推）」
独立路径互证；可行性断言（m 上限等）自证抽法合法。
fobar_invertible：已知概率与 N,k,n，反解 m —— 在合法整数格上唯一命中。

_clean 纪律：分母 C(N,n) ≤ 99（N ≤ 8 时恒成立）。
题干与参数结构全部自造（抽象产品/次品语境），不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "prob.classical"
KP_NAME = "古典概型与排列组合"
PACK_ID = "probability"
CACHE_NS = "family-prob.classical:v1"
FAMILY = "hypergeo"

# 参数格：24 组手选 (N, k, n, m)——全部满足可行性约束（m ≤ min(k,n) 且 n-m ≤ N-k）
_GRID: list[tuple[int, int, int, int]] = [
    (6, 1, 2, 0), (6, 1, 4, 0), (6, 2, 3, 1), (7, 1, 2, 0),
    (7, 1, 3, 0), (7, 1, 4, 0), (8, 1, 2, 0), (8, 1, 3, 0),
    (8, 2, 4, 1), (6, 1, 2, 1), (6, 1, 4, 1), (6, 2, 2, 0),
    (6, 2, 2, 1), (6, 2, 2, 2), (6, 2, 4, 0), (6, 2, 4, 1),
    (6, 2, 4, 2), (6, 3, 2, 1), (6, 3, 4, 2), (6, 4, 2, 0),
    (6, 4, 2, 1), (6, 4, 2, 2), (6, 4, 3, 2), (6, 4, 4, 2),
]
GRID: dict[str, list] = {"p4": _GRID}

_MAX_Q = 99


def _feasible(N: int, k: int, n: int, m: int) -> bool:
    """抽法可行：0≤m≤min(k,n) 且 n-m ≤ N-k（合格品够抽）且 n ≤ N。"""
    return (0 <= m <= min(k, n)) and (0 <= n - m <= N - k) and 0 < n <= N and 0 < k < N


def hypergeo(N: int, k: int, n: int, m: int) -> dict | None:
    """超几何：N 件含 k 件次品，任取 n 件恰有 m 件次品的概率。"""
    if not _feasible(N, k, n, m):
        return None
    denom = sp.binomial(N, n)
    if not (denom.is_Integer and 1 <= denom <= _MAX_Q):
        return None
    num = sp.binomial(k, m) * sp.binomial(N - k, n - m)
    answer = sp.simplify(num / denom)

    def _independent_path() -> sp.Rational:
        """独立路径：逐件连乘（不放回抽样的条件概率递推）。"""
        # P(指定次品序列) 求和：从组合数定义展开逐项连乘
        total = sp.Rational(0)
        # m 件次品在 n 次中的位置枚举（与组合计数等价的第二路径）
        import itertools
        good, bad = N - k, k
        cnt = 0
        for positions in itertools.combinations(range(n), m):
            p_acc = sp.Rational(1)
            g, b = good, bad
            for i in range(n):
                if i in positions:
                    p_acc *= sp.Rational(b, g + b)
                    b -= 1
                else:
                    p_acc *= sp.Rational(g, g + b)
                    g -= 1
            total += p_acc
            cnt += 1
        return total

    def _fobar_unique(value) -> bool:
        """已知概率与 N,k,n，在合法 m 整数格上唯一命中。"""
        hits = []
        for mm in range(0, min(k, n) + 1):
            if _feasible(N, k, n, mm):
                num2 = sp.binomial(k, mm) * sp.binomial(N - k, n - mm)
                if sp.simplify(num2 / denom - value) == 0:
                    hits.append(mm)
        return hits == [m]

    stem = (f"已知一批产品共 $N = {N}$ 件，其中次品 $k = {k}$ 件。"
            f"现从中任取 $n = {n}$ 件，则恰好取到 $m = {m}$ 件次品的概率为 ______。")

    ans_note = (f"超几何模型：P = C({k},{m})·C({N - k},{n - m}) / C({N},{n}) "
                f"= {sp.latex(num)} / {denom} = {sp.latex(answer)}。")
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"N": N, "k": k, "n": n, "m": m},
        "difficulty": "基础" if n <= 2 else "进阶",
        "statement_md": stem, "analysis": ans_note,
        "answer_sympy": sp.sstr(answer),
        "assert": [
            # 抽法可行
            ("draw_feasible", _feasible(N, k, n, m)),
            # 分母（样本点总数）干净
            ("denom_clean", 1 <= denom <= _MAX_Q),
            # 独立路径：逐件连乘递推 == 组合计数
            ("independent_path", sp.simplify(_independent_path() - answer) == 0),
            # 概率合法
            ("answer_in_range", 0 < answer <= 1),
            # fobar 反解唯一
            ("fobar_invertible", _fobar_unique(answer)),
        ],
    }


SPEC: dict[str, object] = {FAMILY: hypergeo}


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """枚举参数格，返回通过全部合法性断言的前 limit 个实例（确定性，无随机）。"""
    gen = SPEC[family]
    out = []
    for combo in GRID["p4"]:
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
        print("  ", it["statement_md"][:96], "| ans:", it["answer_sympy"])
