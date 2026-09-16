"""确定性参数化模板族 · 协方差（kp: prob.cov.corr）。

缓存命名空间：family-prob.cov.corr:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
两点联合分布 (X,Y) ∈ {(x1,y1),(x2,y2)}，P = p 与 1−p：
Cov(X,Y) = E[XY] − E[X]E[Y] Rational 精确；定义式 Σ(x−EX)(y−EY)p 双路径互证。
判决：**matrix-extension:A**（可全构造 → 绿标家族）。

家族 cov2pt：两点离散联合分布。
  E[X] = x1·p + x2·(1−p)，E[Y] 同构，E[XY] = x1y1·p + x2y2·(1−p)，
  Cov = E[XY] − E[X]·E[Y]。
构造即正确：计算公式与定义式两路互证。
fobar 不适用（两点分布在 p 与 1−p 下 |Cov| 相同，反解不唯一），已披露；
以「符号一致性」断言替代（Cov 正负号与相关方向一致）。

_clean 纪律：概率分母 ≤2 位；Cov 为有理数，分母 ≤2 位（可为负）。
题干与参数结构全部自造（抽象联合分布），不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "prob.cov.corr"
KP_NAME = "协方差与相关系数"
PACK_ID = "probability"
CACHE_NS = "family-prob.cov.corr:v1"
FAMILY = "cov2pt"

# 参数格：24 组手选 (x1,y1, x2,y2, p)——正负协方差、正/反相关覆盖
_GRID: list[tuple[int, int, int, int, sp.Rational]] = [
    (0, 1, 1, 0, sp.Rational(1, 2)), (0, 1, 1, 0, sp.Rational(1, 4)),
    (0, 1, 1, 0, sp.Rational(3, 4)), (1, 1, 2, 2, sp.Rational(1, 2)),
    (1, 1, 2, 2, sp.Rational(1, 4)), (1, 1, 2, 2, sp.Rational(3, 4)),
    (1, 2, 2, 1, sp.Rational(1, 2)), (1, 2, 2, 1, sp.Rational(1, 4)),
    (1, 2, 2, 1, sp.Rational(3, 4)), (1, 0, 2, 3, sp.Rational(1, 2)),
    (1, 0, 2, 3, sp.Rational(1, 4)), (1, 0, 2, 3, sp.Rational(3, 4)),
    (0, 0, 1, 1, sp.Rational(1, 2)), (0, 0, 1, 1, sp.Rational(1, 4)),
    (0, 0, 1, 1, sp.Rational(3, 4)), (0, 2, 2, 0, sp.Rational(1, 2)),
    (0, 2, 2, 0, sp.Rational(1, 4)), (0, 2, 2, 0, sp.Rational(3, 4)),
    (1, 3, 3, 1, sp.Rational(1, 2)), (1, 3, 3, 1, sp.Rational(1, 4)),
    (2, 0, 3, 2, sp.Rational(1, 2)), (2, 0, 3, 2, sp.Rational(1, 4)),
    (1, 1, 3, 3, sp.Rational(1, 2)), (1, 1, 3, 3, sp.Rational(1, 4)),
]
GRID: dict[str, list] = {"p5": _GRID}

_MAX_Q = 99


def _clean_prob(p) -> bool:
    p = sp.simplify(p)
    return bool(p.is_Rational and 0 < p < 1 and 1 <= p.q <= _MAX_Q)


def cov2pt(x1: int, y1: int, x2: int, y2: int, p) -> dict | None:
    """两点联合分布的 Cov(X,Y)（计算公式×定义式双路互证）。"""
    p = sp.Rational(p)
    if not _clean_prob(p):
        return None
    q = 1 - p
    ex = x1 * p + x2 * q
    ey = y1 * p + y2 * q
    exy = x1 * y1 * p + x2 * y2 * q
    cov_formula = sp.simplify(exy - ex * ey)
    cov_def = sp.simplify((x1 - ex) * (y1 - ey) * p + (x2 - ex) * (y2 - ey) * q)
    cov = sp.simplify(cov_formula)

    def _sign_consistent() -> bool:
        """fobar 不适用（两点分布在 p 与 1−p 下 |Cov| 相同，反解不唯一，已披露）；
        替代断言：Cov 的符号与 (x2−x1)(y2−y1) 方向一致（正/负相关判定正确）。"""
        return cov == 0 or sp.sign(cov) == sp.sign(sp.Integer((x2 - x1) * (y2 - y1)))

    stem = (f"设二维离散型随机变量 $(X, Y)$ 的联合分布为："
            f"$P(X = {x1}, Y = {y1}) = {sp.latex(p)}$，"
            f"$P(X = {x2}, Y = {y2}) = {sp.latex(q)}$，"
            f"则 $\\mathrm{{Cov}}(X, Y) =$ ______。")

    ans_note = (f"Cov(X,Y) = E[XY] − E[X]·E[Y] = {sp.latex(exy)} − {sp.latex(ex)}·{sp.latex(ey)} "
                f"= {sp.latex(cov)}；"
                + ("正相关方向（X 增 Y 趋增）。" if cov > 0 else
                   "负相关方向（X 增 Y 趋减）。" if cov < 0 else "不线性相关。"))
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "p": str(p)},
        "difficulty": "基础",
        "statement_md": stem, "analysis": ans_note,
        "answer_sympy": sp.sstr(cov),
        "assert": [
            # 分布合法
            ("probs_valid", _clean_prob(p)),
            # 双路恒等：计算公式 == 定义式
            ("dual_identity", sp.simplify(cov_formula - cov_def) == 0),
            # 非退化：两点取值不同（否则 X 或 Y 退化为常数，Cov 恒 0）
            ("non_degenerate", not (x1 == x2 and y1 == y2)),
            ("clean_answer", sp.simplify(cov).is_Rational
             and 1 <= abs(sp.Rational(sp.together(cov).as_numer_denom()[0])) < 10**9),
            ("sign_consistent", _sign_consistent()),
        ],
    }


SPEC: dict[str, object] = {FAMILY: cov2pt}


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
        print("  ", it["statement_md"][:110], "| ans:", it["answer_sympy"])
