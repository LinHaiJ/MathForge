"""确定性参数化模板族 · 分部积分（kp: calc.integral.byparts）。

缓存命名空间：family-calc.integral.byparts:v1

定档依据（§5 步骤 2）：cache/sympy_matrix.json 的 6 类未覆盖本 kp →
按 §3 同法做 SymPy 探针（matrix-extension）：
  ∫_0^1 x e^{kx} dx  → 1/k² + (k-1)e^k/k²，含 e^k，答案不干净（弃用）
  ∫_0^π x sin(kx) dx → (-1)^{k+1}·π/k，有理数乘 π，干净且参数格互异（采用）
  ∫_1^a ln x dx      → a ln a - a + 1，含 ln a（弃用）
判决：**matrix-extension:A**（可全构造 → 绿标家族）。

家族 byparts_xsin：I(a,k) = ∫_0^π a·x·sin(kx) dx = a·(-1)^{k+1}·π/k。
构造即正确——答案不由 LLM 写出，而是 SymPy 从原函数
F(x) = a(sin(kx)/k² - x cos(kx)/k) 求导反查 + 牛顿-莱布尼茨代入双路推导。

`_clean` 纪律：沿用 families.py 语义（答案分母 ≤2 位 / 简单形式），本包按
任务书原文「分母 ≤2 位」取 q ≤ 99，并放行「有理数 × π」这一简单符号形态
（root families.py 只处理纯有理答案，故那里 q ≤ 12 且不含 π；本族答案必然带 π）。

题干与参数结构全部自造，不引用任何真题原文。
"""

from __future__ import annotations

import itertools

import sympy as sp

KP_ID = "calc.integral.byparts"
KP_NAME = "分部积分"
PACK_ID = "calculus"
CACHE_NS = "family-calc.integral.byparts:v1"
FAMILY = "byparts_xsin"

X, K_SYM = sp.symbols("x kk")

# 参数格：a=被积函数系数，k=正弦频率（4×6=24 格，全格合法）
GRID: dict[str, list[int]] = {"a": [1, 2, 3, 4], "k": [1, 2, 3, 4, 5, 6]}

_MAX_Q = 99  # 分母 ≤2 位


def _clean(expr) -> bool:
    """答案干净度：整数 / 分母 ≤2 位的分数 / 上述两者乘 π。"""
    expr = sp.simplify(expr)
    if expr.is_Integer:
        return True
    if expr.is_Rational:
        return 1 <= expr.q <= _MAX_Q
    coeff = sp.simplify(expr / sp.pi)
    if coeff.is_Rational:
        return 1 <= coeff.q <= _MAX_Q
    return False


def _fobar_unique(a: int, k: int, value) -> bool:
    """fobar 反解断言：已知系数 a 与答案值，反推频率 k——
    solve 非空 ∧ 逆向唯一 ∧ 反解落回参数格。"""
    sols = sp.solve(sp.Eq(a * sp.pi / K_SYM, sp.Abs(value)), K_SYM)
    hits = [s for s in sols if s.is_real and s.is_Integer and int(s) in GRID["k"]]
    if len(hits) != 1 or int(hits[0]) != k:
        return False
    # 符号由 k 的奇偶唯一决定：k 奇 → 正，k 偶 → 负
    return sp.sign(value) == (1 if k % 2 == 1 else -1)


def byparts_xsin(a: int, k: int) -> dict | None:
    """∫_0^π a·x·sin(kx) dx —— 分部积分（u=x, dv=sin(kx)dx）典型形态。"""
    if a <= 0 or k <= 0:
        return None
    integrand = a * X * sp.sin(k * X)
    # 分部积分原函数（构造侧闭式，非 LLM 产出）
    F = a * (sp.sin(k * X) / k**2 - X * sp.cos(k * X) / k)
    val = sp.simplify(F.subs(X, sp.pi) - F.subs(X, 0))
    if not _clean(val):
        return None
    closed = a * sp.Integer(-1) ** (k + 1) * sp.pi / k
    stem = (f"计算定积分 $\\displaystyle\\int_{{0}}^{{\\pi}} {sp.latex(integrand)}\\,\\mathrm{{d}}x=$ "
            f"______（结果用 $\\pi$ 表示）。")
    return {
        "kp": KP_NAME, "family": FAMILY, "params": {"a": a, "k": k},
        "difficulty": "基础" if a == 1 else "进阶",
        "statement_md": stem, "answer_sympy": sp.sstr(val),
        "assert": [
            # 原函数正确性：F'(x) 必须等于被积函数（分部积分构造自证）
            ("antiderivative", sp.simplify(sp.diff(F, X) - integrand) == 0),
            # 牛顿-莱布尼茨值与闭式 a(-1)^{k+1}π/k 一致（两路推导对账）
            ("closed_form", sp.simplify(val - closed) == 0),
            # 被积函数在 [0,π] 上无奇点（分部积分适用前提）
            ("integrand_finite", all(sp.simplify(integrand.subs(X, t)).is_finite
                                     for t in (0, sp.pi / 2, sp.pi))),
            ("clean_answer", _clean(val)),
            ("fobar_invertible", _fobar_unique(a, k, val)),
        ],
    }


# 模块级常量 spec（对齐 root families.py 的 _families_spec 契约）
SPEC: dict[str, object] = {FAMILY: byparts_xsin}


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """枚举参数格，返回通过全部合法性断言的实例（确定性，无随机）。"""
    gen = SPEC[family]
    keys = list(GRID)
    out = []
    for combo in itertools.product(*(GRID[k] for k in keys)):
        if len(out) >= limit:
            break
        q = gen(**dict(zip(keys, combo)))
        if q and all(bool(v) for _, v in q["assert"]):
            out.append(q)
    return out


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    items = enumerate_family(limit=24)
    print(f"{FAMILY}: {len(items)} 合法实例 / 24 参数格")
    for it in items[:3]:
        print("  ", it["statement_md"][:70], "| ans:", it["answer_sympy"])
