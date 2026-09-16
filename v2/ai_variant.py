# AI 变式引擎（任务书 P1，2026-09-15）
#
# 三权分立：调度不生成（P1-P6 选考点）· 生成不验证（LLM 只写外壳）· 验证不调度（SymPy 终审）。
# 流程：数学核（确定性族实例，不可改）→ 考察计划（LLM，计划缺失即拒）→ 变式生成（核不变型：
# contextual 情境化 / multistep 多步拆分）→ 三闸门（结构 / 数学保真 / 多样性）→ 下发。
# 红线：
#   1. 换问型（reworded，答案目标改变）一期禁止——需独立重算通道后再启用。
#   2. 判分权永不交给 LLM：答案恒为核答案，数学对错由 verify.check_answer 判定。
#   3. 变量改名不是漂移：自由符号数相同且重命名后 SymPy 等价 = 同构保真（2026-09-13 实测教训）。
# 依赖方向：本模块不 import v2api（v2api → ai_variant 单向）；kernel 由调用方构造传入。

from __future__ import annotations

import re

# ---- 可调闸门阈值（2026-09-13 实测口径） ----
STEM_MIN = 40
STEM_MAX = 400
SIM_KERNEL_MAX = 0.90   # 变式与核的去参模板相似度上限（超过=换壳失败）
SIM_SIB_MAX = 0.85      # 变式两两相似度上限（超过=重复生成）
PLAN_MIN_LEN = 8

_KINDS = ("contextual", "multistep")

SYSTEM_PLAN = """你是考研数学命题组长。给定一道「母题」的不可更改数学核（参数与标准答案）与变式策略，
输出一份**考察计划**（不写题目本身）。计划必须说明：这道变式考什么能力、解题要经过哪几步、
给什么类型的学生设什么坑。硬约束：数学内核（参数与答案）不得改动；计划 ≤120 字。
输出 JSON：{"assess":"考什么","approach":"解题路径","trap":"给谁的坑","ok":true}"""

SYSTEM_VARIANTS = """你是考研数学命题人。依据给定的考察计划，把母题改写成 {k} 道变式题。
硬约束：
1. 数学内核不可改：变式题与母题的数学结构、参数、所考知识点、最终目标完全一致（允许换情境/换记法）。
2. 不得引入新的未知量、不得改变参数取值。
3. 只允许两种类型：contextual（换成真实情境，数学结构不变）、multistep（题干用一句引导性叙述
   点明「先求什么、再求什么」的解题路径，**但全题只保留一个填空**，且该空必须是最终目标）。
   **禁止把中间量设为独立小问或额外填空**，**禁止改变求解目标**。
4. 题干中文叙述 + 行内 LaTeX（$...$），40-160 字，**全题恰好一个 ______ 填空**，
   不得在题干泄露答案，不得出现解题提示。
   **情境只提供背景**：任何需要解题者自己做出的关键判断（如「分母为零」「分子分母同阶」
   「事件互斥」），不得以任何形式写进题干——那是剧透，不是情境（2026-09-17 盲评 bad case）。
5. **不要输出答案**——答案由系统从数学核直接注入，你只负责题干（这保证答案零出错）。
输出 JSON：{{"variants":[{{"kind":"contextual|multistep","stem_md":"...","note":"一句话改动说明"}}]}}"""

_HEX = re.compile(r"\b[sy]p\.\w+\b")


# ---------------------------------------------------------------- 闸门 ----
def _mask(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"-?\d+(?:\.\d+)?", "#", s or "")).strip()


def _ngrams(s: str, n: int = 3) -> set:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


def jaccard(a: str, b: str) -> float:
    A, B = _ngrams(a), _ngrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def gate_structure(v: dict) -> tuple[bool, str]:
    """G1：字段齐、长度合规、KaTeX 括号配对、无内部符号泄漏、恰好单空（判分模型是单空填空）。"""
    for k in ("kind", "stem_md", "answer_expr"):
        if not str(v.get(k) or "").strip():
            return False, f"缺字段 {k}"
    if v.get("kind") not in _KINDS:
        return False, f"kind {v.get('kind')} 非核不变型"
    stem = v["stem_md"]
    if not (STEM_MIN <= len(stem) <= STEM_MAX):
        return False, f"题干长度 {len(stem)} 越界"
    if stem.count("$") % 2 != 0:
        return False, "LaTeX $ 未配对"
    if stem.count("{") != stem.count("}"):
        return False, "花括号未配对"
    if _HEX.search(stem):
        return False, "泄漏内部符号"
    # 单空纪律（2026-09-16 用户实测：multistep 生成三问三空，但判分只认最终答案 → 语义断裂）
    if stem.count("____") != 1:
        return False, f"填空数 {stem.count('____')} != 1（一期只支持单空）"
    return True, "ok"


def _sym_normalize_free(expr: str):
    import sympy as sp
    from verify import normalize_expr
    return sp.sympify(normalize_expr(expr))


def gate_math(v: dict, kernel: dict) -> tuple[bool, str]:
    """G2：数学保真。严格等价（G2a）或同构重命名等价（G2b，改名不是漂移）。"""
    from verify import check_answer
    try:
        if check_answer(v["answer_expr"], kernel["answer_sympy"]):
            return True, "等价"
    except Exception as e:  # noqa: BLE001
        return False, f"校验异常 {str(e)[:60]}"
    # G2b：同构重命名（母题 x、LLM 写 t 的情形）
    try:
        import sympy as sp
        a = _sym_normalize_free(v["answer_expr"])
        b = _sym_normalize_free(kernel["answer_sympy"])
        fa = sorted(a.free_symbols, key=str)
        fb = sorted(b.free_symbols, key=str)
        if len(fa) == len(fb) and fa:
            a2 = a.subs(dict(zip(fa, fb)), simultaneous=True)
            if sp.simplify(a2 - b) == 0:
                return True, "同构等价（变量改名）"
        return False, "漂移：与核答案不等价"
    except Exception:  # noqa: BLE001
        return False, "漂移：与核答案不等价"


def gate_diverse(stem: str, kernel_stem: str, siblings: list[str]) -> tuple[bool, str]:
    """G3：与核、与已收变式都不构成换壳失败/重复。"""
    if jaccard(_mask(stem), _mask(kernel_stem)) >= SIM_KERNEL_MAX:
        return False, "与母题句式过近（换壳失败）"
    for s in siblings:
        if jaccard(_mask(stem), _mask(s)) >= SIM_SIB_MAX:
            return False, "与已收变式重复"
    return True, "ok"


# ---------------------------------------------------------------- 主流程 ----
def make_ai_variants(kernel: dict, k: int = 3, strategy: str = "contextual",
                     usage_count: int | None = None,
                     namespace: str = "ai_variant") -> dict:
    """计划 → 生成 → 三闸门。任一环节失败返回 ok=False（调用方降级参数扰动）。

    kernel: _try_pack_family 产出的核实例（statement_md/answer_sympy/params/analysis/family/...）。
    namespace: LLM 缓存命名空间——评测用独立命名空间（ai_variant_eval）防污染生产缓存。
    """
    from llm import chat_json  # 延迟导入：离线/无 key 时模块仍可加载

    k = max(1, min(k, 5))
    freq_note = (f"该知识点历年真题出现 {usage_count} 次，命题偏好高频经典形态。"
                 if isinstance(usage_count, int) and usage_count > 0
                 else "该知识点历年真题出现较少，可尝试冷门但合规的情境。")

    # ① 考察计划（计划先行：强制模型想清楚，也供 UI 透出「为什么这么变」）
    plan = None
    try:
        plan = chat_json(
            [{"role": "system", "content": SYSTEM_PLAN},
             {"role": "user", "content":
                 f"母题：{kernel.get('statement_md')}\n"
                 f"内核参数：{json_s(kernel.get('params'))}\n"
                 f"标准答案（不可改）：{kernel.get('answer_sympy')}\n"
                 f"内核解析：{str(kernel.get('analysis') or '')[:200]}\n"
                 f"变式策略：{strategy}。{freq_note}"}],
            temperature=0, namespace=namespace,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "plan", "reason": f"计划生成失败：{str(e)[:80]}"}
    if not isinstance(plan, dict) or not plan.get("ok") \
            or len(str(plan.get("assess") or "")) < PLAN_MIN_LEN:
        return {"ok": False, "stage": "plan", "reason": "考察计划不合规"}

    # ② 生成
    try:
        data = chat_json(
            [{"role": "system", "content": SYSTEM_VARIANTS.format(k=k)},
             {"role": "user", "content":
                 f"母题：{kernel.get('statement_md')}\n"
                 f"内核参数：{json_s(kernel.get('params'))}\n"
                 f"标准答案（不可改，等价即可）：{kernel.get('answer_sympy')}\n"
                 f"考察计划：{json_s(plan)}\n"
                 f"请产出 {k} 道变式（类型混合分配）。"}],
            temperature=0.9, namespace=namespace,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "generate", "reason": f"生成失败：{str(e)[:80]}"}
    raw = (data.get("variants") or []) if isinstance(data, dict) else []
    if not raw:
        return {"ok": False, "stage": "generate", "reason": "无变式产出"}

    # ③ 三闸门
    # 答案由系统注入（核答案），不取 LLM 的 answer_expr——LLM 出错面缩到只剩题干。
    # LLM 若仍返回 answer_expr 则保留并过 G2 校验（作为「AI 自觉度」参考记录）。
    passed, g1 = [], 0
    siblings: list[str] = []
    rejected = 0
    g2_pass = 0
    for v in raw:
        if not v.get("answer_expr"):
            v["answer_expr"] = kernel.get("answer_sympy")
            v["answer_injected"] = True
        ok1, why1 = gate_structure(v)
        if not ok1:
            rejected += 1
            continue
        g1 += 1
        ok2, why2 = gate_math(v, kernel)
        if not ok2:
            rejected += 1
            continue
        g2_pass += 1
        ok3, why3 = gate_diverse(v.get("stem_md", ""), kernel.get("statement_md", ""), siblings)
        if not ok3:
            rejected += 1
            continue
        siblings.append(v["stem_md"])
        passed.append({**v, "g2_kind": ("exact" if why2 == "等价" else "isomorphic")})

    return {
        "ok": bool(passed),
        "plan": {kk: plan.get(kk) for kk in ("assess", "approach", "trap")},
        "variants": passed,
        "stats": {"raw": len(raw), "g1_pass": g1, "g2_pass": g2_pass,
                  "rejected": rejected, "passed": len(passed)},
    }


def json_s(o) -> str:
    import json
    try:
        return json.dumps(o, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(o)
