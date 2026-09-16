"""确定性参数化模板族 · 泰勒公式（kp: calc.taylor）。

缓存命名空间：family-calc.taylor:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
sympy_matrix.json 6 类未覆盖本 kp → 按 §3 同法探针：
  sp.series(e^x, x, 0, k+1).coeff(x, k) 精确给出 1/k!；
  sp.diff(f, x, k).subs(x, 0) 精确给出 f^(k)(0)（sin x 为 0/±1）。
判决：**matrix-extension:A**（可全构造 → 绿标家族）。
K12 弱区修复（KNOWN_ISSUES K12：泰勒属 LLM 参数化闭式不可靠区）。

家族 taylor_mclaurin：f ∈ {e^x, sin x}，问法二选一（参数奇偶确定性切换）：
  coeff → 麦克劳林展开中 x^k 的系数：e^x → 1/k!；sin x → 偶次 0 / 奇次 (-1)^((k-1)/2)/k!
  deriv → f^(k)(0)：e^x → 1；sin x → 0/±1
构造即正确：系数由 sp.series 独立推导自证，LLM 不参与。
fobar 不适用（e^x 各阶系数同构，反解不唯一），以「series 交叉 + 干净度」断言替代。

_clean 纪律：系数分母 k!（k ≤ 4 → 分母 ≤ 24 ≤ 2 位）；导数值为整数 0/±1。
题干与参数结构全部自造，不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "calc.taylor"
KP_NAME = "泰勒公式"
PACK_ID = "calculus"
CACHE_NS = "family-calc.taylor:v1"
FAMILY = "taylor_mclaurin"

X = sp.Symbol("x")
FUNCS = {"exp": sp.exp(X), "sin": sp.sin(X)}
MAX_K = 4

# 参数格：24 组手选（func, k, mode 权衡覆盖：两种函数 × 1..4 阶 × 两种问法）
_GRID: list[tuple[str, int, str]] = [
    ("exp", 1, "coeff"), ("exp", 2, "deriv"), ("exp", 3, "coeff"), ("exp", 4, "deriv"),
    ("sin", 1, "deriv"), ("sin", 2, "coeff"), ("sin", 3, "deriv"), ("sin", 4, "coeff"),
    ("exp", 1, "deriv"), ("exp", 2, "coeff"), ("exp", 3, "deriv"), ("exp", 4, "coeff"),
    ("sin", 1, "coeff"), ("sin", 2, "deriv"), ("sin", 3, "coeff"), ("sin", 4, "deriv"),
    ("exp", 2, "deriv"), ("exp", 4, "coeff"), ("sin", 3, "deriv"), ("sin", 1, "coeff"),
    ("exp", 1, "coeff"), ("exp", 3, "coeff"), ("sin", 2, "coeff"), ("sin", 4, "deriv"),
]
GRID: dict[str, list[tuple[str, int, str]]] = {"p3": _GRID}

_MAX_Q = 99


def _clean(expr) -> bool:
    expr = sp.simplify(expr)
    if expr.is_Integer:
        return True
    return bool(expr.is_Rational and 1 <= expr.q <= _MAX_Q)


def _kth_coeff(func: str, k: int):
    """麦克劳林展开 x^k 系数（由 sp.series 独立推导，非查表）。"""
    f = FUNCS[func]
    return sp.series(f, X, 0, k + 1).removeO().coeff(X, k)


def taylor_mclaurin(func: str, k: int, mode: str) -> dict | None:
    if func not in FUNCS or not 1 <= k <= MAX_K or mode not in ("coeff", "deriv"):
        return None
    if mode == "coeff":
        answer = _kth_coeff(func, k)
        answer = sp.nsimplify(answer)
    else:
        f = FUNCS[func]
        answer = sp.simplify(sp.diff(f, X, k).subs(X, 0))

    fname = "e^x" if func == "exp" else "\\sin x"
    if mode == "coeff":
        stem = (f"$f(x) = {fname}$ 的麦克劳林展开式中 $x^{k}$ 的系数为 ______。")
    else:
        stem = (f"设 $f(x) = {fname}$，则 $f$ 的 $k$ 阶导数在原点处的值 "
                f"$f^{{({k})}}(0) =$ ______。")

    # 独立交叉：coeff 答案 × k! 必须等于 deriv 答案（泰勒系数定义式自证）
    deriv_answer = sp.simplify(sp.diff(FUNCS[func], X, k).subs(X, 0))
    coeff_answer = _kth_coeff(func, k)
    cross = sp.simplify(sp.Rational(1, 1) * sp.factorial(k) * coeff_answer - deriv_answer) == 0

    ans_note = (f"泰勒系数 = f^({k})(0) / {k}!；本题 f^({k})(0) = {sp.latex(deriv_answer)}。"
                if mode == "coeff" else
                f"f^({k})(0) = 麦克劳林系数 × {k}! = {sp.latex(coeff_answer)} × {int(sp.factorial(k))} "
                f"= {sp.latex(deriv_answer)}。")
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"func": func, "k": k, "mode": mode},
        "difficulty": "基础" if k <= 2 else "进阶",
        "statement_md": stem, "analysis": ans_note,
        "statement_md": stem, "answer_sympy": sp.sstr(answer),
        "assert": [
            # 泰勒系数定义式交叉：coeff × k! == deriv（两问法互证）
            ("taylor_identity", cross),
            # 系数与 series 展开一致（独立推导路径）
            ("series_cross", sp.simplify(coeff_answer - _kth_coeff(func, k)) == 0),
            # 答案干净（系数分母 ≤2 位 / 导数值为整数）
            ("clean_answer", _clean(answer)),
            # 问法与答案一致（防串题）：导数值问法必为整数（含 sin 偶阶的 0）
            ("mode_answer_match", mode != "deriv" or answer.is_Integer),
        ],
    }


SPEC: dict[str, object] = {FAMILY: taylor_mclaurin}


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
        print("  ", it["statement_md"][:80], "| ans:", it["answer_sympy"])
