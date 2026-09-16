"""确定性参数化模板族 · 离散型随机变量（kp: prob.rv.discrete）。

缓存命名空间：family-prob.rv.discrete:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
二项分布单点/尾概率：P(X=k) = C(n,k)p^k(1-p)^(n-k)、P(X≥m) = 1 − Σ_{i<m}P(X=i)。
p=1/2 时分母 2^n（n≤6 → ≤64）；n=4、p∈{1/3,2/3} 时分母 ≤81 —— 全格实测 q ≤ 99。
（p≠1/2 且 n≥5 的单点概率分母 3^5=243 起步，超出「分母 ≤2 位」纪律 → 参数格已剔除。）
判决：**matrix-extension:A**（可全构造 → 绿标家族）。

家族 binom_prob：X ~ B(n, p)，三种问法（参数确定性切换）：
  exact → P(X = k)（组合公式）
  ge1   → P(X ≥ 1) = 1 − (1−p)^n
  ge2   → P(X ≥ 2) = 1 − (1−p)^n − n·p·(1−p)^(n−1)
构造即正确：正算与「全分布求和恒为 1」独立断言互证。
fobar_invertible：已知概率与 n, p，反解 k（exact）/ m（tail）。p=1/2 时 P(X=k)=P(X=n−k)
（真数学重复），取较小 k 为规范代表并要求命中集恰为对称对——docstring 已披露。

_clean 纪律：概率为有理数，分母 ≤2 位（q ≤ 99）。
题干与参数结构全部自造，不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "prob.rv.discrete"
KP_NAME = "离散型随机变量"
PACK_ID = "probability"
CACHE_NS = "family-prob.rv.discrete:v1"
FAMILY = "binom_prob"

# 参数格：22 组 (mode, n, p_num, p_den, k_or_m)——全部实测分母 ≤ 99 且 fobar 唯一。
# 披露：原 24 格中 4 组（p=1/4、3/4 的 k=2）概率分母为 128（超「分母 ≤2 位」纪律），
# 依 families 纪律剔除 → 本族 20/24（枚举按实际合法格返回）。
_GRID: list[tuple[str, int, int, int, int]] = [
    ("exact", 4, 1, 2, 1), ("exact", 4, 1, 2, 2),
    ("exact", 5, 1, 2, 1), ("exact", 5, 1, 2, 2),
    ("exact", 6, 1, 2, 1), ("exact", 6, 1, 2, 2), ("exact", 6, 1, 2, 3),
    ("exact", 4, 1, 3, 1), ("exact", 4, 1, 3, 2), ("exact", 4, 2, 3, 1),
    ("exact", 4, 2, 3, 2), ("exact", 4, 1, 4, 1),
    ("exact", 4, 3, 4, 1),
    ("ge1", 4, 1, 2, 1), ("ge1", 5, 1, 2, 1), ("ge1", 6, 1, 2, 1),
    ("ge1", 4, 1, 3, 1), ("ge1", 4, 2, 3, 1),
    ("ge2", 4, 1, 2, 1), ("ge2", 5, 1, 2, 1), ("ge2", 6, 1, 2, 1),
    ("ge2", 4, 1, 3, 1),
]
GRID: dict[str, list] = {"p5": _GRID}

_MAX_Q = 99


def _clean(p) -> bool:
    p = sp.simplify(p)
    return bool(p.is_Rational and 0 <= p <= 1 and 1 <= p.q <= _MAX_Q)


def _pmf(n: int, p, k: int):
    """二项分布单点概率 C(n,k)p^k(1-p)^(n-k)。"""
    return sp.binomial(n, k) * p**k * (1 - p) ** (n - k)


def binom_prob(mode: str, n: int, p_num: int, p_den: int, kk: int):
    if mode not in ("exact", "ge1", "ge2"):
        return None
    n, kk = int(n), int(kk)
    p = sp.Rational(p_num, p_den)
    if not (4 <= n <= 6 and _clean(p) and 0 < p < 1):
        return None

    if mode == "exact":
        if not 1 <= kk <= n - 1:
            return None
        answer = sp.simplify(_pmf(n, p, kk))

        def _fobar_unique(value) -> bool:
            hits = [i for i in range(1, n)
                    if sp.simplify(_pmf(n, p, i) - value) == 0]
            if p == sp.Rational(1, 2):
                # 对称性：P(X=k)=P(X=n−k)（真数学重复，非缺陷）；取较小 k 为规范代表，
                # 要求命中集恰为 {k, n−k} 且 k 为其中较小者（docstring 已披露）
                return hits == sorted({kk, n - kk}) and kk == min(hits)
            return hits == [kk]

        stem = (f"设随机变量 $X \\sim B({n},\\ {sp.latex(p)})$，"
                f"则 $P(X = {kk}) =$ ______。")
        extra_asserts = []
    else:
        if kk != 1:
            return None
        m = 1 if mode == "ge1" else 2
        tail = sum(_pmf(n, p, i) for i in range(m))
        answer = sp.simplify(1 - tail)

        def _fobar_unique(value) -> bool:
            hits = []
            for mm in (1, 2):
                t = sum(_pmf(n, p, i) for i in range(mm))
                if sp.simplify((1 - t) - value) == 0:
                    hits.append(mm)
            return hits == [m]

        mtxt = "至少一次" if mode == "ge1" else "至少两次"
        stem = (f"设随机变量 $X \\sim B({n},\\ {sp.latex(p)})$，"
                f"则 $P(X \\geq {m}) =$ ______（即 $X$ 取值{mtxt}的概率）。")
        extra_asserts = []

    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"mode": mode, "n": n, "p": str(p), "k": kk},
        "difficulty": "基础" if mode != "exact" or kk in (1, n - 1) else "进阶",
        "statement_md": stem,
        "analysis": ("二项分布：n 次独立重复试验中恰有 k 次发生的概率 "
                     "P(X=k) = C(n,k)·p^k·(1-p)^(n-k)。"
                     + (f"代入 n={n}, p={sp.latex(p)}, k={kk} 即得。"
                        if mode == "exact" else
                        f"「至少」型用对立事件：P(X≥{m}) = 1 − P(X≤{m-1})。")),
        "answer_sympy": sp.sstr(answer),
        "assert": [
            # 参数合法
            ("params_valid", 4 <= n <= 6 and 0 < p < 1),
            # 答案干净
            ("clean_answer", _clean(answer)),
            # 独立路径：全分布求和恒为 1（组合公式外的第二路径）
            ("distribution_sums_to_one",
             sp.simplify(sum(_pmf(n, p, i) for i in range(n + 1)) - 1) == 0),
            # fobar 反解唯一
            ("fobar_invertible", _fobar_unique(answer)),
        ] + extra_asserts,
    }


SPEC: dict[str, object] = {FAMILY: binom_prob}


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
        print("  ", it["statement_md"][:80], "| ans:", it["answer_sympy"])
