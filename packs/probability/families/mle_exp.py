"""确定性参数化模板族 · 极大似然估计（kp: prob.estimate）。

缓存命名空间：family-prob.estimate:v1

定档依据：cache/sympy_matrix.json classes.F_mle → verdict=A
（对数似然解析可导、驻点可解、二阶导判最大值；fobar 可反解）。
任务书 §5 续推（2026-09-12 夜间）：概率包此前 0 族 → 首批补族之一。

家族 mle_exp_rate：X ~ Exp(λ)（密度 λe^{-λx}, x>0），样本量 n=5，
观测值 obs（全为正整数）。logL(λ) = n·lnλ − λ·Σx，驻点 λ̂ = n/Σx，
二阶导 −n/λ² < 0 → 最大值。构造即正确：λ̂ 由「驻点方程符号求解 +
二阶导凹性」双断言自证，LLM 不参与任何推导。

参数格：24 组手选观测值（Σx 覆盖 5..20，含同和不同序组防记忆题面）。
_clean 纪律：λ̂ = 5/Σx 全为有理数，q ≤ 20 ≤ 99。
题干与参数结构全部自造，不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "prob.estimate"
KP_NAME = "参数估计"
PACK_ID = "probability"
CACHE_NS = "family-prob.estimate:v1"
FAMILY = "mle_exp_rate"

N = 5  # 样本量固定

# 参数格：24 组观测值（Σx 覆盖 5..20；同和不同序防「背题面」）
_GRID_OBS: list[tuple[int, ...]] = [
    (1, 1, 1, 1, 1),  # S=5
    (2, 1, 1, 1, 1),  # S=6
    (1, 2, 1, 2, 1),  # S=7
    (2, 1, 2, 2, 1),  # S=8
    (2, 1, 2, 1, 3),  # S=9
    (2, 3, 1, 2, 2),  # S=10
    (1, 2, 3, 3, 2),  # S=11
    (3, 2, 2, 3, 2),  # S=12
    (3, 3, 4, 2, 1),  # S=13
    (4, 2, 3, 3, 2),  # S=14
    (3, 3, 3, 3, 3),  # S=15
    (4, 3, 3, 3, 3),  # S=16
    (4, 4, 3, 3, 3),  # S=17
    (4, 4, 4, 3, 3),  # S=18
    (4, 4, 4, 4, 3),  # S=19
    (4, 4, 4, 4, 4),  # S=20
    (1, 2, 2, 2, 1),  # S=8（换序）
    (2, 2, 1, 3, 2),  # S=10（换序）
    (2, 3, 2, 2, 3),  # S=12（换序）
    (1, 2, 3, 1, 2),  # S=9（换序）
    (2, 3, 3, 4, 2),  # S=14（换序）
    (3, 4, 3, 3, 3),  # S=16（换序）
    (3, 3, 4, 2, 1),  # S=13（换序）
    (3, 4, 4, 4, 3),  # S=18（换序）
]
GRID: dict[str, list[tuple[int, ...]]] = {"obs": _GRID_OBS}

_MAX_Q = 99


def _clean(expr) -> bool:
    """答案干净度：整数 / 分母 ≤2 位的分数。"""
    expr = sp.simplify(expr)
    if expr.is_Integer:
        return True
    return bool(expr.is_Rational and 1 <= expr.q <= _MAX_Q)


def mle_exp_rate(obs: tuple[int, ...]) -> dict | None:
    """指数分布 MLE：λ̂ = n/Σx，驻点 + 凹性双断言自证。"""
    if len(obs) != N or any(int(x) <= 0 for x in obs):
        return None
    s_total = sum(int(x) for x in obs)
    lam_hat = sp.Rational(N, s_total)

    lmb = sp.Symbol("lambda_", positive=True)
    log_l = N * sp.log(lmb) - lmb * s_total
    stationary = sp.solve(sp.diff(log_l, lmb), lmb)
    second = sp.diff(log_l, lmb, 2).subs(lmb, lam_hat)

    def _fobar_unique(value) -> bool:
        """fobar 反解断言：已知 λ̂ 反解 Σx——solve 非空 ∧ 唯一 ∧ 落回参数格。"""
        sols = sp.solve(sp.Eq(sp.Rational(N, 1) / sp.Symbol("Ss"), value), sp.Symbol("Ss"))
        hits = [s for s in sols if s.is_real and s.is_Integer
                and int(s) == s_total]
        return len(hits) == 1

    obs_latex = ",\\ ".join(sp.latex(int(x)) for x in obs)
    stem = (f"设总体 $X$ 服从参数为 $\\lambda\\ (\\lambda > 0)$ 的指数分布，"
            f"$X_1, X_2, \\dots, X_{N}$ 为来自 $X$ 的简单随机样本，"
            f"其观测值为 $({obs_latex})$，"
            f"则 $\\lambda$ 的极大似然估计 $\\hat{{\\lambda}} =$ ______。")

    ans_note = (f"对数似然 lnL(λ) = {N}·lnλ − λ·{s_total}；令 d(lnL)/dλ = 0 得 "
                f"{N}/λ = {s_total}，即 λ̂ = {sp.latex(lam_hat)}（二阶导为负，是极大值点）。")
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"obs": list(obs), "S": s_total},
        "difficulty": "基础" if s_total <= 8 else "进阶",
        "statement_md": stem, "analysis": ans_note,
        "statement_md": stem, "answer_sympy": sp.sstr(lam_hat),
        "assert": [
            # 观测合法：全为正数（指数分布支撑集要求）
            ("obs_valid", all(int(x) > 0 for x in obs)),
            # 驻点方程 d(logL)/dλ = n/λ − S = 0 的解恰为 λ̂
            ("mle_stationary", stationary == [lam_hat]),
            # 凹性：二阶导在 λ̂ 处为负 → 驻点是最大值点
            ("mle_maximum", second.is_negative),
            ("clean_answer", _clean(lam_hat)),
            ("fobar_invertible", _fobar_unique(lam_hat)),
        ],
    }


# 模块级常量 spec（对齐 root families.py 的 _families_spec 契约）
SPEC: dict[str, object] = {FAMILY: mle_exp_rate}


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """枚举参数格，返回通过全部合法性断言的实例（确定性，无随机）。"""
    gen = SPEC[family]
    keys = list(GRID)
    out = []
    for combo in itertools_product(*(GRID[k] for k in keys)):
        if len(out) >= limit:
            break
        q = gen(**dict(zip(keys, combo)))
        if q and all(bool(v) for _, v in q["assert"]):
            out.append(q)
    return out


def itertools_product(*iterables):
    """极简 product（避免为 24 格引入 itertools 依赖差异，行为与 itertools.product 一致）。"""
    pools = [tuple(p) for p in iterables]
    result = [()]
    for pool in pools:
        result = [x + (y,) for x in result for y in pool]
    return result


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    items = enumerate_family(limit=24)
    print(f"{FAMILY}: {len(items)} 合法实例 / 24 参数格")
    for it in items[:3]:
        print("  ", it["statement_md"][:72], "| ans:", it["answer_sympy"])
