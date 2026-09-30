"""确定性参数化模板族 · 罗尔定理（kp: calc.rolle）。

缓存命名空间：family-calc.rolle:v2
　　代次语义：v1 = 旧问法 rolle_xi（解 ξ）；v2 = 问法重锚 rolle_roots（2026-09-29，I 模块）。

== 家族 rolle_roots（问法重锚：罗尔承重型）================================

背景：旧 rolle_xi 问法「f(x)=a(x-p)(x-q)，求 f'(x)=0 在 (p,q) 内的解 ξ」解方程
即可得，罗尔定理在解题路径中不承重（考点装饰性问法；真题从不这样考）。真题承重
形态是「f 有 n 个互异零点 → f'(x)=0 的根个数/分布」：罗尔保证每相邻零点间的开区间
内至少一根，导数次数封顶根的上界（如 2008 数二：f(x)=x²(x-1)(x-2)，求 f'(x) 的
零点个数）。

家族 rolle_roots：f(x) = k·(x-a₁)(x-a₂)(x-a₃)(x-a₄)，a₁<a₂<a₃<a₄ 为互异整数
（|aᵢ| ≤ 5），k 为非零小整数（|k| ≤ 3，可为负——k 的符号/大小不影响根个数，
只作参数变化感，不作第三问法，不贪）。
  问法一 ask="count"（主）：「则方程 f'(x)=0 在实数范围内不同实根的个数为____」→ 3。
  问法二 ask="interval"（双问法定案）：「则 f'(x)=0 在区间 (a₁,a₄) 内不同实根的
    个数为____」→ 3。
    定案理由：三个开区间 (aᵢ,aᵢ₊₁) 恰是 (a₁,a₄) 的一个划分，罗尔给出 ≥3 根、
    三次多项式封顶 ≤3 根，夹逼出「恰 3 且全部落在 (a₁,a₄) 内」——两问法同构
    同答案，考的都是定理承重链而非解方程。

构造即正确（SymPy 断言链，每实例）：f 恰 4 个互异实零点（罗尔前提）→ f' 恰 3 个
实根、每个开区间 (aᵢ,aᵢ₊₁) 内恰 1 根（罗尔下界）→ (a₁,a₄) 两侧外部无根（区间
问法合法性）。答案由断言链直接钉死，LLM 不参与。
退化过滤：根非互异 / 展开系数丑形态（f' 展开后 |coeff| > 200）的格不产出。

v1 兼容（红线）：repo 根 families.py 的 rolle_xi（v1 问法代次）原样保留、零改动；
v1 演示线（generate._FAMILY_KP → root families.enumerate_family）不受本文件影响。
本模块保留 rolle_xi 委托入口（enumerate_family("rolle_xi")）仅作 v1 行为核对用；
v2 家族直出路径（v2api._try_pack_family）自 v2 代次起只产出 rolle_roots 实例。

历史（D8-6）：本文件曾是 root rolle_xi 的「包内化」委托壳（CACHE_NS v1，2026-09-29
起由 rolle_roots 自包含实现取代）。

运行上下文：模块顶层 `import families` 依赖 repo 根在 sys.path。app 从根启动 /
pytest 从根跑均满足；动态加载（pack_loader）时本文件也会把 repo 根插入 sys.path，
故 import 失败路径已被兜底。
"""

from __future__ import annotations

import os
import sys

# 把 repo 根（packs/calculus/families → 上溯 4 级）补进 sys.path，保证 `import families`
# 可用（pack_loader 以独立 module name 动态加载本文件，顶层 import 需根在 path）。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import families  # noqa: E402  —— v1 repo 根确定性族模块（只读委托，rolle_xi 兼容入口用）

import sympy as sp

KP_ID = "calc.rolle"
KP_NAME = "罗尔定理"
PACK_ID = "calculus"
CACHE_NS = "family-calc.rolle:v2"   # 问法重锚代次：rolle_xi(v1) → rolle_roots(v2)
FAMILY = "rolle_roots"
LEGACY_FAMILY = "rolle_xi"          # v1 root 族名（兼容核对入口，非 v2 产出）

X = sp.Symbol("x")

_MAX_COEFF = 200   # 丑形态过滤：f'(x) 展开系数绝对值上限（题干/解析渲染卫生）
_MAX_ROOT = 5      # 参数格纪律：根绝对值 ≤ 5（互异整数，覆盖正/负/跨零）
_MAX_K = 3         # 系数纪律：|k| ≤ 3 的非零小整数（根个数与 k 无关）

# 参数格：16 组手选 (roots, k, ask)。4 根互异整数覆盖正负/含零/全正/全负/对称，
# k ∈ {±1,±2,±3}，ask 双问法交替（确定性切换，仿 taylor 族 mode 参数先例）。
_GRID: list[tuple[tuple[int, int, int, int], int, str]] = [
    ((-3, -1, 1, 3), 1, "count"),       # 对称跨零（基准格）
    ((-2, -1, 1, 2), 1, "interval"),
    ((-1, 0, 1, 2), 1, "count"),        # 连续三根含 0
    ((-4, -1, 2, 5), 1, "interval"),    # 全跨度
    ((-5, -3, 1, 3), 2, "count"),
    ((-2, 0, 2, 4), 2, "interval"),
    ((-3, -2, 1, 4), 3, "count"),
    ((-1, 1, 2, 5), 2, "interval"),
    ((-4, -2, 0, 3), 1, "count"),
    ((-5, -1, 3, 4), 3, "interval"),
    ((0, 1, 2, 3), 1, "count"),         # 全正
    ((1, 2, 3, 4), 2, "interval"),      # 全正无零
    ((-5, -4, -2, -1), 1, "count"),     # 全负
    ((-3, -2, -1, 1), -1, "interval"),  # 负系数（k 符号不影响根个数）
    ((-2, -1, 2, 3), -1, "count"),
    ((-4, -3, 3, 5), 2, "interval"),
]
GRID: dict[str, list[tuple[tuple[int, int, int, int], int, str]]] = {"roots_k_ask": _GRID}

_ASKS = ("count", "interval")


def _factor_latex(a: int) -> str:
    """一次因式 LaTeX：手拼避免 SymPy 展开式渲染瑕疵（"+ -"/"1x^" 等）。"""
    if a == 0:
        return "x"
    return f"(x - {a})" if a > 0 else f"(x + {-a})"


def rolle_roots(roots: tuple[int, int, int, int], k: int, ask: str) -> dict | None:
    """f(x)=k·Π(x-aᵢ)（4 互异整数根）→ f'(x)=0 根个数（罗尔承重，构造即正确）。"""
    a1, a2, a3, a4 = roots
    if ask not in _ASKS:
        return None
    if not (k != 0 and abs(k) <= _MAX_K):
        return None
    if not all(isinstance(r, int) and abs(r) <= _MAX_ROOT for r in roots):
        return None
    if not (a1 < a2 < a3 < a4):   # 互异 + 严格递增（罗尔前提的结构保证）
        return None

    f = k * sp.prod([X - r for r in roots])
    fp = sp.diff(f, X)
    P, dP = sp.Poly(f, X), sp.Poly(fp, X)

    n_zeros_f = P.count_roots()                    # f 的互异实零点数（应 4）
    n_roots_fp = dP.count_roots()                  # f'(x)=0 实根数（应 3）
    per_gap = [dP.count_roots(roots[i], roots[i + 1]) for i in range(3)]
    outside = dP.count_roots(-sp.oo, a1) + dP.count_roots(a4, sp.oo)

    fp_coeffs = [abs(int(c)) for c in dP.all_coeffs()]
    if max(fp_coeffs) > _MAX_COEFF:                # 丑形态过滤（展开解析渲染卫生）
        return None

    f_latex = ("" if k == 1 else "-" if k == -1 else str(k)) \
        + "".join(_factor_latex(r) for r in roots)
    span_latex = f"({sp.latex(a1)},{sp.latex(a4)})"

    if ask == "count":
        stem = (f"设函数 $f(x) = {f_latex}$，"
                f"则方程 $f'(x)=0$ 在实数范围内不同实根的个数为 ______。")
        tail = ("又 $f'(x)$ 是三次多项式，至多 3 个实根，夹逼得恰 3 个。"
                "（系数 $k\\ne 0$ 只作整体缩放，不影响零点与根个数。）")
    else:  # interval
        stem = (f"设函数 $f(x) = {f_latex}$，"
                f"则 $f'(x)=0$ 在区间 ${span_latex}$ 内的不同实根个数为 ______。")
        tail = ("由 ≤3 与 ≥3 夹逼，这 3 个根全部落在区间 "
                f"${span_latex}$ 内（两侧外部无根），故区间内恰 3 个。"
                "（系数 $k\\ne 0$ 只作整体缩放，不影响零点与根个数。）")

    fp_latex = sp.latex(sp.expand(fp))
    analysis = (
        f"罗尔承重链：$f$ 在 4 个互异点 ${sp.latex(a1)}<{sp.latex(a2)}<{sp.latex(a3)}<"
        f"{sp.latex(a4)}$ 处取零，相邻零点把区间分成 3 段，$f$ 在每段两端等值（均为 0）"
        f"→ 罗尔定理保证 $f'(x)=0$ 在每个开区间内至少一根，共 ≥3 个；"
        f"其中 $f'(x)={fp_latex}$。" + tail
    )

    return {
        "kp": KP_NAME, "kp_id": KP_ID, "family": FAMILY,
        "params": {"roots": list(roots), "k": k, "ask": ask},
        "difficulty": "基础" if abs(k) == 1 else "进阶",
        "statement_md": stem, "analysis": analysis,
        "answer_sympy": sp.sstr(sp.Integer(3)),
        "verify_level": "green",
        "assert": [
            # 结构：4 根互异严格递增、k 小整数非零（_clean 纪律）
            ("roots_distinct_strict", a1 < a2 < a3 < a4
             and all(abs(r) <= _MAX_ROOT for r in roots)),
            ("k_small_nonzero", k != 0 and abs(k) <= _MAX_K),
            # 罗尔前提：f 确有 4 个互异实零点（count_roots 独立复核，非查表）
            ("f_four_real_zeros", n_zeros_f == 4
             and all(f.subs(X, r) == 0 for r in roots)),
            # 罗尔下界：每个开区间 (aᵢ,aᵢ₊₁) 内恰 1 根
            ("rolle_each_gap_one_root", per_gap == [1, 1, 1]),
            # 结论：f'(x)=0 恰 3 个实根（下界 3 与次数上界 3 夹逼）
            ("fprime_three_real_roots", n_roots_fp == 3),
            # 区间问法合法性：3 根全部落在 (a₁,a₄) 内，两侧外部无根
            ("no_roots_outside_span", outside == 0),
            # 答案与断言链一致（两问法答案恒 3）
            ("answer_is_count", n_roots_fp == 3 and sp.sstr(sp.Integer(n_roots_fp)) == "3"),
            # 渲染卫生：展开导数系数不过丑
            ("clean_shape", max(fp_coeffs) <= _MAX_COEFF),
        ],
    }


SPEC: dict[str, object] = {FAMILY: rolle_roots, LEGACY_FAMILY: None}


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """枚举参数格，返回通过全部合法性断言的前 limit 个实例（确定性，无随机）。

    family="rolle_roots"（默认，v2 产出）：本模块自包含枚举；
    family="rolle_xi"（v1 兼容核对入口）：委托 repo 根 families（只读，v2 路径不使用）。
    未知名不抛裸 KeyError，返回 []（收口为「枚举不出实例」语义，与 _try_pack_family
    的空产出 → 回落 v1/LLM 兜底链一致；仓库内调用方均传合法名，纯收紧）。
    """
    if family == LEGACY_FAMILY:
        return _enumerate_rolle_xi(limit)
    gen = SPEC.get(family)
    if gen is None:            # 未知名 → 空产出（不裸抛，见上注释）
        return []
    out = []
    for combo in GRID["roots_k_ask"]:
        if len(out) >= limit:
            break
        q = gen(*combo)
        if q and all(bool(v) for _, v in q["assert"]):
            out.append(q)
    return out


def _enumerate_rolle_xi(limit: int = 24) -> list[dict]:
    """v1 兼容委托：root families 的 rolle_xi（问法代次 v1，仅供核对/回退比对）。"""
    items = families.enumerate_family(LEGACY_FAMILY, limit)
    out = []
    for it in items:
        if it and all(bool(v) for _, v in it.get("assert", [])):
            it = dict(it)
            it.setdefault("kp_id", KP_ID)
            it.setdefault("verify_level", "green")
            out.append(it)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    items = enumerate_family(limit=24)
    print(f"{FAMILY}: {len(items)} 合法实例 / {len(_GRID)} 参数格（CACHE_NS {CACHE_NS}）")
    for it in items[:4]:
        print("  ", it["statement_md"][:78], "| ans:", it["answer_sympy"])
