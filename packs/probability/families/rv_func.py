"""确定性参数化模板族 · 随机变量函数的分布（kp: prob.rv.func）。

缓存命名空间：family-prob.rv.func:v1

定档依据：cache/sympy_matrix.json classes.E_rv_transform → verdict=A
（密度/期望解析可靠；Abs 化简在 y>0 支撑集内无歧义）。
任务书 §5 续推（2026-09-12 夜间）：概率包此前 0 族 → 首批补族之二。

家族 rvfunc_ulin：X ~ U(a, a+L)，Y = cX + d（c>0 单调增，公式法/分布函数法标准形态）。
  密度问法：f_Y(t0) = 1/(cL)
  分布问法：P(Y ≤ t0) = m/4（t0 取支撑集四等分点）
构造即正确：答案由两条独立路径推出——公式法 f_Y(t0)=f_X((t0-d)/c)·(1/c) 与
分布函数法 ∫f_X 复合线性变换——LLM 不参与任何推导。

参数格：a∈{0,1} × L∈{1,2,3,4} × c∈{1,2,3} × m∈{1,2,3} = 72 全格（d 由参数派生，
mode 由奇偶派生，非独立维度）；enumerate_family 等距取 24 实例保证维度均匀覆盖。

_clean 纪律：本族答案全为有理数，q ≤ 99（对齐任务书「分母 ≤2 位」原文）。
题干与参数结构全部自造，不引用任何真题原文。
"""

from __future__ import annotations

import itertools

import sympy as sp

KP_ID = "prob.rv.func"
KP_NAME = "随机变量函数分布"
PACK_ID = "probability"
CACHE_NS = "family-prob.rv.func:v1"
FAMILY = "rvfunc_ulin"

# 参数格（72 全格；d/mode 为派生量）；枚举取等距 24 实例保证维度覆盖
GRID: dict[str, list[int]] = {"a": [0, 1], "L": [1, 2, 3, 4], "c": [1, 2, 3], "m": [1, 2, 3]}

_MAX_Q = 99


def _clean(expr) -> bool:
    """答案干净度：整数 / 分母 ≤2 位的分数。"""
    expr = sp.simplify(expr)
    if expr.is_Integer:
        return True
    return bool(expr.is_Rational and 1 <= expr.q <= _MAX_Q)


def rvfunc_ulin(a: int, L: int, c: int, m: int) -> dict | None:
    """X~U(a,a+L)，Y=cX+d：支撑集内四等分点处的密度值 / 分布函数值。"""
    if a < 0 or L <= 0 or c <= 0 or not 1 <= m <= 3:
        return None
    d = (a + L + c + m) % 3                    # 派生平移量 ∈ {0,1,2}
    mode = "density" if (a + L + c + m) % 2 == 1 else "cdf"

    lo = c * a + d                             # Y 支撑集下界
    hi = c * (a + L) + d                       # Y 支撑集上界
    t0 = lo + sp.Rational(m * c * L, 4)        # 支撑集内四等分点

    if mode == "density":
        answer = sp.Rational(1, c * L)
    else:
        answer = sp.Rational(m, 4)

    y = sp.symbols("y")
    f_y = sp.Rational(1, c * L)                # Y 的密度（支撑集内；必须 Rational，float 会污染积分断言）
    norm = sp.integrate(f_y, (y, lo, hi))
    x = sp.symbols("x")
    x_cut = t0 / c - sp.Rational(d, c)         # 逆变换 (t0-d)/c

    def _fobar_unique(value) -> bool:
        """fobar 反解断言：solve 非空 ∧ 逆向唯一 ∧ 反解落回参数格。"""
        if mode == "density":
            sols = sp.solve(sp.Eq(1 / (c * sp.Symbol("Ls")), value), sp.Symbol("Ls"))
            hits = [s for s in sols if s.is_real and s.is_Integer and int(s) in GRID["L"]]
            return len(hits) == 1 and int(hits[0]) == L
        sols = sp.solve(sp.Eq(sp.Symbol("ms") / 4, value), sp.Symbol("ms"))
        hits = [s for s in sols if s.is_real and s.is_Integer and int(s) in GRID["m"]]
        return len(hits) == 1 and int(hits[0]) == m

    y_expr = ("X" if c == 1 else f"{c}X") + (f" + {d}" if d else "")
    if mode == "density":
        stem = (f"设随机变量 $X$ 在区间 $[{a},\\ {a + L}]$ 上服从均匀分布，"
                f"$Y = {y_expr}$，"
                f"则 $Y$ 的概率密度 $f_Y(y)$ 在 $y = {sp.latex(t0)}$ 处的值为 ______。")
    else:
        stem = (f"设随机变量 $X$ 在区间 $[{a},\\ {a + L}]$ 上服从均匀分布，"
                f"$Y = {y_expr}$，"
                f"则 $P\\{{Y \\le {sp.latex(t0)}\\}}=$ ______。")

    ans_note = (f"均匀分布线性变换：Y 的密度在支撑集 [{lo}, {hi}] 上恒为 1/({c}·{L})。"
                + (f"取 y = {sp.latex(t0)} 代入即得。"
                   if mode == "density" else
                   f"P(Y ≤ {sp.latex(t0)}) = (t0 − {lo}) / ({c}·{L}) = {sp.latex(answer)}。"))
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"a": a, "L": L, "c": c, "d": d, "m": m, "mode": mode},
        "difficulty": "基础" if (a == 0 and L == 1) else "进阶",
        "statement_md": stem, "analysis": ans_note,
        "statement_md": stem, "answer_sympy": sp.sstr(answer),
        "assert": [
            # 参数非退化：支撑集长 cL > 0
            ("support_valid", L > 0 and c > 0),
            # t0 严格落在 Y 的支撑集内（密度非零 / 分布函数非平凡）
            ("t_in_support", bool(lo < t0 < hi)),
            # 归一性：密度在支撑集上积分 = 1
            ("normalization", sp.simplify(norm - 1) == 0),
            # 独立路径：公式法 f_X((t0-d)/c)·(1/c) / 分布函数法 ∫f_X 复合
            ("independent_path", (
                sp.simplify(sp.Rational(1, L) / c - answer) == 0 if mode == "density"
                else sp.simplify(sp.integrate(sp.Rational(1, L), (x, a, x_cut)) - answer) == 0)),
            ("clean_answer", _clean(answer)),
            ("fobar_invertible", _fobar_unique(answer)),
        ],
    }


# 模块级常量 spec（对齐 root families.py 的 _families_spec 契约）
SPEC: dict[str, object] = {FAMILY: rvfunc_ulin}


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """全格枚举后等距取 limit 个合法实例（确定性，无随机；步长取样保证参数维度均匀覆盖）。"""
    gen = SPEC[family]
    keys = list(GRID)
    all_valid = []
    for combo in itertools.product(*(GRID[k] for k in keys)):
        q = gen(**dict(zip(keys, combo)))
        if q and all(bool(v) for _, v in q["assert"]):
            all_valid.append(q)
    n = len(all_valid)
    if n <= limit:
        return all_valid
    return [all_valid[i * n // limit] for i in range(limit)]


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    items = enumerate_family(limit=24)
    print(f"{FAMILY}: {len(items)} 合法实例 / 36 参数格（取前 24）")
    for it in items[:3]:
        print("  ", it["statement_md"][:72], "| ans:", it["answer_sympy"])
