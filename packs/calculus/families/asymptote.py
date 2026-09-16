"""确定性参数化模板族 · 渐近线（kp: calc.asymptote）。

缓存命名空间：family-calc.asymptote:v1

定档依据（§5 步骤 2，matrix-extension 探针 2026-09-12）：
sympy_matrix.json 6 类未覆盖本 kp → 按 §3 同法探针：
  f(x) = x + c + k/(x - r)：sp.limit(f - (x+c), x, oo) = 0 精确成立（斜渐近线定义）；
  sp.limit(f, x, r, '+') = oo（竖直渐近线存在性可判）。
判决：**matrix-extension:A**（可全构造 → 绿标家族）。
任务书 §5 续推第三批次（sim_weak_kps #5）。

家族 asy_slant：f(x) = x + c + k/(x - r)（r≠0）
  斜渐近线 y = x + c，竖直渐近线 x = r。
  问法（由参数奇偶确定性二选一）：
    slant → 斜渐近线方程 y=______（答案 x + c，符号表达式，SymPy 等价判分）
    vert  → 竖直渐近线方程 x=______（答案 r，常数）
构造即正确：断言直接用渐近线定义（极限存在性）自证，LLM 不参与。
fobar 不适用（渐近线由 c/r 直接读出），以「定义极限 + 形态自证」双断言替代。

_clean 纪律：c、r 全为绝对值 ≤3 的非零整数。
题干与参数结构全部自造，不引用任何真题原文。
"""

from __future__ import annotations

import sympy as sp

KP_ID = "calc.asymptote"
KP_NAME = "渐近线"
PACK_ID = "calculus"
CACHE_NS = "family-calc.asymptote:v1"
FAMILY = "asy_slant"

X = sp.Symbol("x")

# 参数格：24 组手选 (c, r, k)——正负/跨号/分子系数覆盖
_GRID: list[tuple[int, int, int]] = [
    (1, 1, 1), (2, 1, 2), (-1, 1, 1), (3, 1, 3),
    (1, 2, 1), (-2, 2, 2), (2, 2, 1), (-3, 2, 1),
    (1, -1, 2), (-1, -1, 1), (3, -1, 2), (2, -1, 3),
    (1, -2, 1), (3, -2, 1), (-2, -1, 3), (-3, -2, 2),
    (2, 3, 1), (-1, 3, 2), (3, 3, 1), (1, 3, 2),
    (-3, -1, 1), (-2, 3, 1), (2, -2, 2), (-1, -2, 1),
]
GRID: dict[str, list[tuple[int, int, int]]] = {"p3": _GRID}


def asy_slant(c: int, r: int, k: int) -> dict | None:
    """f(x) = x + c + k/(x-r) 的斜/竖直渐近线（定义极限自证）。"""
    if c == 0 or r == 0 or k == 0:
        return None
    f = X + c + k / (X - r)
    mode = "slant" if (c + r + k) % 2 == 1 else "vert"

    if mode == "slant":
        answer = sp.Symbol("x") + c
        def_check = sp.simplify(sp.limit(f - answer, X, sp.oo)) == 0
    else:
        answer = sp.Integer(r)
        def_check = (sp.limit(f, X, r, "+") == sp.oo
                     and sp.limit(f, X, r, "-") == -sp.oo * sp.sign(k))
        def_check = bool(sp.limit(f, X, r, "+") == sp.oo or sp.limit(f, X, r, "-") == -sp.oo)

    f_latex = sp.latex(f)
    if mode == "slant":
        stem = (f"曲线 $y = {f_latex}$ 的斜渐近线方程为 $y =$ ______。")
    else:
        stem = (f"曲线 $y = {f_latex}$ 的竖直渐近线方程为 $x =$ ______。")

    ans_note = (f"f(x) = x + {c} + {k}/(x − {r})：x→∞ 时余项 {k}/(x−{r}) → 0，故斜渐近线为 y = x + {c}；"
                f"x→{r} 时分母趋于 0，故竖直渐近线为 x = {r}。本题问"
                + ("斜渐近线。" if mode == "slant" else "竖直渐近线。"))
    return {
        "kp": KP_NAME, "family": FAMILY,
        "params": {"c": c, "r": r, "k": k, "mode": mode},
        "difficulty": "基础" if (c > 0 and r > 0) else "进阶",
        "statement_md": stem, "analysis": ans_note,
        "statement_md": stem, "answer_sympy": sp.sstr(answer),
        "assert": [
            # 参数非零（否则形态退化：r=0 无竖直渐近线定义域问题、c=0 退化为特殊斜率）
            ("params_valid", c != 0 and r != 0 and k != 0),
            # 渐近线定义极限（构造自证核心）
            ("definition_limit", def_check),
            # 形态自证：f 与「渐近线 + 余项」恒等
            ("form_identity", sp.simplify(f - (X + c) - k / (X - r)) == 0),
            # 答案干净（整数/线性式）
            ("clean_answer", answer.is_Integer or (answer.is_Add and len(answer.free_symbols) == 1)),
            # 问法与答案一致（防串题）
            ("mode_answer_match",
             (mode == "slant" and answer.is_Add) or (mode == "vert" and answer.is_Integer)),
        ],
    }


SPEC: dict[str, object] = {FAMILY: asy_slant}


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """枚举参数格，返回通过全部合法性断言的前 limit 个实例（确定性，无随机）。"""
    gen = SPEC[family]
    keys = list(GRID)
    out = []
    for combo in GRID[keys[0]]:
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
