"""MathForge 出题引擎（T6）：加载出题专家 skill + 真题 few-shot 锚 + 双级验证。

- 🟢 绿标（计算题）：LLM 产出参数化模板，标准答案由 SymPy 从参数计算（构造即正确），
  再加一次 LLM 盲解对账（独立求解 vs SymPy 答案，check_answer 判等）拦截题干-答案错位。
- 🟡 黄标（概念/排序/选择）：生成后由两次独立 LLM 盲做，答案一致才通过（PRD §7）。
- 全部 LLM 调用走 llm.py 磁盘缓存；验证失败的题绝不外推（推题纪律）。
"""

from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

from llm import chat_json
from families import enumerate_family
from verify import check_answer, is_valid_expr

# Skill ablation 开关（P0-2 A/B 实验）：MATHFORGE_ABLATION=1 时剥离 SKILL.md，
# 其余 prompt 完全一致；缓存命名空间同步升版防串（默认关闭，行为与基线完全一致）。
_ABLATION = os.environ.get("MATHFORGE_ABLATION") == "1"
_NS_GREEN = "gen-green-ab:v1:a{}:" if _ABLATION else "gen-green:v5:a{}:"
_NS_YELLOW = "gen-yellow-ab:v1:a{}:" if _ABLATION else "gen-yellow:v4:a{}:"


def generate_from_family(spec: dict) -> dict:
    """K1/Day3 修复路径：确定性模板族出题（families.py）。

    数学正确性由 SymPy 从题目结构推导保证（断言内建于 spec）；
    LLM 仅负责解析实例化；盲解对账降级为 advisory——若判官与构造不一致，
    题目仍外推但打 judge_flag 留档（构造可证 > 判官直觉）。
    """
    q = {
        "kp": spec["kp"], "difficulty": spec["difficulty"], "qtype": "calculation",
        "verify_level": "green", "statement_md": spec["statement_md"],
        "answer_sympy": spec["answer_sympy"], "answer_md": None,
        "options": None, "correct": None, "family": spec["family"], "params": spec["params"],
        "sympy_asserts": {k: bool(v) for k, v in spec["assert"]},
    }
    if not all(q["sympy_asserts"].values()):
        q["status"] = "blocked_pending_human"
        q["error"] = "SymPy 断言失败（不该发生：家族枚举已过滤）"
        return q
    solver_ans, raw = _blind_solve(q["statement_md"], None)
    agree = solver_ans is not None and check_answer(solver_ans, spec["answer_sympy"])
    if not agree:
        s2, raw2 = _blind_solve(q["statement_md"], None, verify_mode=True)
        agree = s2 is not None and check_answer(s2, spec["answer_sympy"])
        raw += f" | 复核解={s2}"
    q["verification"] = {"solver_agree": bool(agree), "solver_ans": solver_ans, "raw": raw[:200]}
    if not agree:
        q["judge_flag"] = "solver_mismatch_but_sympy_provable"
    q["analysis"] = _instantiate_analysis(q)
    return q

_HERE = Path(__file__).resolve().parent
SKILL_PATH = _HERE / "skills" / "math-examiner" / "SKILL.md"
QUESTION_POOL = _HERE / "题集" / "cxyonly.jsonl"


def load_skill() -> str:
    if _ABLATION:  # B 臂：剥离 SKILL.md（A/B 实验对照臂）
        return ""
    return SKILL_PATH.read_text(encoding="utf-8")


def pick_few_shot(kp: str, qtype: str, k: int = 2) -> list[str]:
    """从真题池选 k 道风格锚：优先同题型，其次同科目。题集缺失时返回空（skill 降级用 §四风格锚）。"""
    if not QUESTION_POOL.exists():
        return []
    pool = [json.loads(l) for l in QUESTION_POOL.read_text(encoding="utf-8").splitlines() if l.strip()]
    want_type = "single_choice" if qtype == "choice" else "subjective"

    def score(q):
        s = 0
        if q["type"] == want_type:
            s += 2
        if kp and any(tok and tok in (q["kp"] or "") for tok in kp.split()):
            s += 3
        if q.get("analysis"):
            s += 1
        return -s

    ranked = sorted(pool, key=score)
    out = []
    for q in ranked:
        if len(out) >= k:
            break
        opts = q.get("options")
        if opts:
            parts = [o if isinstance(o, str) else f"{o.get('label', '')}. {o.get('content_md', '')}"
                     for o in opts]
            opts = f"\n选项：{' | '.join(p for p in parts if len(p) > 2)}"
        else:
            opts = ""
        out.append(
            f"【真题锚 {len(out)+1}】{q['statement_md']}{opts}\n答案：{q['answer']}\n解析：{q['analysis'][:260]}"
        )
    return out


_GREEN_SYSTEM = """{skill}

【本次任务：出 1 道🟢绿标计算题】额外输出契约（覆盖 §九 中的 answer 字段要求）：
- "statement_template"：题干模板，参数用 {{name}} 占位，条件自含、单题单问。
  参数只出现在数值位，且候选值应使代入后式子自然（系数位建议 1~5 的正整数，
  避免代入后出现 "+ -3x" 或 "+ 0" 的丑形态）；
  禁止在题干中出现"（xx 为常数）"之类元注释，常数项直接省略或自然融入
- "params"：{{"参数名": [候选值1, 候选值2, ...]}}，全部是小整数或简分数，至少 2 个候选
- "answer_expr"：SymPy 语法的标准答案表达式，可含 {{name}} 占位参数（如 "{{a}}**2/3+1"），
  最终值由程序代入参数计算——你必须保证它数学上就是该题的正确答案
【示范（answer_expr 必须真正随参数变化）】
  statement_template: "求 $f(x)={{a}}x^2+{{b}}x$ 在 $x=1$ 处的导数值。"
  params: {{"a": [1,2,3], "b": [1,2]}}，answer_expr: "2*{{a}}+{{b}}"
  反例（禁止）：中值定理"求 ξ"类写不出可靠参数化闭式时，改出导数值/简单积分/极限等直接计算题
只输出 JSON。"""

_YELLOW_SYSTEM = """{skill}

【本次任务：出 1 道🟡黄标{qtype_label}】答案必须是可规范化短串（独立解题者只凭题面作答，必须可判卷）：
- 概念判断→题面必须是"判断如下陈述是否正确：…"，答案只有 正确 或 错误（陈述要客观、无歧义、教科书可判）
- 排序→题面给出带标号的步骤，答案如 B→A→C（用题面标号体系，唯一确定）
- 单项选择→恰好 4 个选项，答案为单个字母
只输出 JSON（按 §九 契约，含 options/correct 若为选择题）。"""

_SOLVE_SYSTEM = """你是独立的考研数学解题者。只根据题面独立作答，禁止臆测出题人意图。
作答前先检查题面前提是否自洽（例：罗尔定理要求端点函数值相等、所求点必须落在给定开区间内；
参数化题目代入具体值后条件是否仍成立）。若发现前提自相矛盾或无解，输出 {"answer": "PREMISE_BROKEN"}。
选择→只输出字母；排序→只输出如 A→B→C（用题面标号体系，输出完整序列）；
判断→只输出 正确 或 错误；计算→只输出最终结果的 SymPy 表达式
（常数用 E/pi、分数用 Rational(a,b)、根号用 sqrt()、自然对数必须写 log() 禁用 ln）。
只输出 JSON：{"answer": "<规范化短串>"}"""

# 渲染瑕疵守卫：题干出现即拒绝（PRD §13 验收线"渲染正确"）
_RENDER_FLAWS = [r"1x\^", r"\+\s*0(?![\.\d])", r"（[^（）]*为常数）", r"\+\s*-",
                 r"1\\(sin|cos|tan|cot|log|ln)"]


def render_flaws(statement: str) -> "str | None":
    """渲染瑕疵检查（2026-09-12：包族直出路径也须过守卫，见 v2api/_try_pack_family）。
    命中返回首个瑕疵的模式说明；干净返回 None。"""
    for pat in _RENDER_FLAWS:
        if re.search(pat, statement or ""):
            return pat
    return None


def _unknown_placeholders(text: str, params: dict) -> set[str]:
    """找出未代入的 {name} 占位符；跳过 LaTeX 命令参数（\\begin{pmatrix}、\\mathbf{A} 等）。

    判据：{ 前若紧跟反斜杠命令（\\word），则该花括号是命令的参数而非占位符。
    （R3 教训：朴素正则把 \\begin{pmatrix} 判成占位符，矩阵域全军覆没。）
    """
    unknown = set()
    for m in re.finditer(r"\{([a-zA-Z_]+)\}", text):
        prefix = text[max(0, m.start() - 24):m.start()]
        if re.search(r"\\[a-zA-Z]+$", prefix):
            continue
        if m.group(1) not in params:
            unknown.add(m.group(1))
    return unknown


def _base_user(kp_context: dict, difficulty: str) -> str:
    parts = [
        f"知识点：{kp_context.get('kp', kp_context.get('name', ''))}",
        f"定义：{kp_context.get('definition_md', '')}",
    ]
    if kp_context.get("key_formulas"):
        parts.append("关键公式：" + "；".join(kp_context["key_formulas"][:4]))
    if kp_context.get("common_mistakes"):
        parts.append("常见错误（可作陷阱素材）：" + "；".join(kp_context["common_mistakes"][:3]))
    parts.append(f"难度目标：{difficulty}")
    return "\n".join(parts)


def _format_few_shot(blocks: list[str]) -> str:
    if not blocks:
        return ""
    return "\n\n【真题风格锚（模仿其表述与难度，禁止抄题）】\n" + "\n\n".join(blocks)


def _blind_solve(statement: str, options: list | None, verify_mode: bool = False) -> tuple[str | None, str]:
    """独立盲做一次，返回规范化答案短串。verify_mode 追加复核指令换缓存键（二次仲裁用）。"""
    user = statement + ("\n（复核模式：请逐步验算后再给最终答案）" if verify_mode else "")
    if options:
        user += "\n选项：" + " | ".join(options)
    try:
        d = chat_json([{"role": "system", "content": _SOLVE_SYSTEM},
                       {"role": "user", "content": user}],
                      temperature=0.0, namespace="solve:")
        return _canon(d.get("answer", "")), json.dumps(d, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 —— 盲解失败按不一致处理
        return None, str(e)


def _canon(s: str) -> str:
    """答案规范化（对账用）：去空白与全角标点，保留 ASCII 括号（Rational(1,3) 等不可破坏），
    统一箭头，修复常见记法（sqrt3→sqrt(3)、ln→log）。"""
    s = (s or "").strip()
    s = re.sub(r"[\s，。；．!！?？、：:\"'“”‘’]+", "", s)  # 保留 ASCII 逗号: Rational(1,3) 不可破坏
    s = s.replace("->", "→").replace("⟶", "→")
    s = re.sub(r"sqrt(\d)", r"sqrt(\1)", s)
    s = s.replace("ln(", "log(")
    return s


def _gen_green(kp_context: dict, difficulty: str, few_shot: list[str], variant_of: str | None = None,
               attempt: int = 1) -> dict:
    sys_prompt = _GREEN_SYSTEM.format(skill=load_skill())
    user = _base_user(kp_context, difficulty) + _format_few_shot(few_shot)
    if variant_of:
        user += f"\n\n【变式要求】以原题为母本做参数扰动（§三），结构与步数不变：\n{variant_of}"
    d = chat_json([{"role": "system", "content": sys_prompt},
                   {"role": "user", "content": user}], temperature=1.0,
                  namespace=_NS_GREEN.format(attempt))

    params = {k: random.choice(v) for k, v in (d.get("params") or {}).items() if v}
    if not params:
        raise ValueError("绿标必须参数化（构造即正确）；零参数题的答案是硬编码值，无法确定性保证，请转黄标题型")
    # 参数代入：题干与 answer_expr 都先做占位替换，再解析（占位符不能直接进 sympify）
    from verify import _parse

    answer_expr = str(d["answer_expr"])
    for k, v in params.items():
        answer_expr = answer_expr.replace("{" + k + "}", "(" + str(v) + ")")
    statement = d["statement_template"]
    for k, v in params.items():
        statement = statement.replace("{" + k + "}", str(v))
    # 兜底：模板用了裸参数名（无占位括号）时走符号代入
    unk_expr = _unknown_placeholders(answer_expr, params) if params else set()
    if unk_expr:
        raise ValueError(f"answer_expr 存在未代入占位符 {sorted(unk_expr)}：{answer_expr[:60]}")
    answer_val = _parse(answer_expr)
    try:
        answer_val = answer_val.subs({__import__("sympy").Symbol(k): v for k, v in params.items()})
    except Exception:  # noqa: BLE001 —— 表达式已无参数符号时代入是空操作
        pass
    unk_stem = _unknown_placeholders(statement, params)
    if unk_stem:
        raise ValueError(f"题干存在未代入参数 {sorted(unk_stem)}：{statement[:60]}")
    for pat in _RENDER_FLAWS:
        if re.search(pat, statement):
            raise ValueError(f"模板渲染瑕疵（{pat}）：{statement[:60]}")
    if not is_valid_expr(str(answer_val)):
        raise ValueError("答案表达式不可解析")
    # 盲解对账：独立 LLM 求解 vs SymPy 构造答案（含题目前提自洽审查）
    solver_ans, raw = _blind_solve(statement, None)
    if solver_ans == "PREMISE_BROKEN":
        raise ValueError(f"盲解判定题目前提矛盾：{statement[:80]}")
    agree = solver_ans is not None and check_answer(solver_ans, str(answer_val))
    if not agree:
        # 复核仲裁：构造侧由 SymPy 背书，盲解偶发算错时给第二次独立求解机会
        solver_ans2, raw2 = _blind_solve(statement, None, verify_mode=True)
        agree = solver_ans2 is not None and check_answer(solver_ans2, str(answer_val))
        raw += f" | 复核解={solver_ans2}"
    if not agree:
        raise ValueError(f"盲解与构造答案不一致（solver={solver_ans}, sympy={answer_val}）")

    return {
        "kp": kp_context.get("kp", ""), "difficulty": difficulty,
        "qtype": "calculation", "verify_level": "green",
        "statement_md": statement, "answer_sympy": str(answer_val), "answer_md": None,
        "options": None, "correct": None,
        "analysis": d.get("analysis", ""), "design_note": d.get("design_note", ""),
        "params": params, "verification": {"solver_agree": True, "solver_raw": raw[:200]},
    }


def _gen_yellow(kp_context: dict, difficulty: str, qtype: str, few_shot: list[str],
                attempt: int = 1) -> dict:
    labels = {"concept": "概念判断", "order": "步骤排序", "choice": "单项选择"}
    sys_prompt = _YELLOW_SYSTEM.format(skill=load_skill(), qtype_label=labels.get(qtype, "概念判断"))
    user = _base_user(kp_context, difficulty) + _format_few_shot(few_shot)
    d = chat_json([{"role": "system", "content": sys_prompt},
                   {"role": "user", "content": user}], temperature=1.0,
                  namespace=_NS_YELLOW.format(attempt))
    claimed = _canon(d.get("correct") or d.get("answer_md") or "")
    if not claimed:
        raise ValueError("黄标题缺答案")
    if qtype == "order" and "→" not in claimed:
        raise ValueError(f"排序题答案必须是完整序列（含→），实际：{claimed}")
    a1, raw1 = _blind_solve(d["statement_md"], d.get("options"))
    a2, raw2 = _blind_solve(d["statement_md"], d.get("options"))
    if a1 != claimed or a2 != claimed:
        raise ValueError(f"双盲不一致（claim={claimed},盲1={a1},盲2={a2}）")
    return {
        "kp": kp_context.get("kp", ""), "difficulty": difficulty,
        "qtype": qtype, "verify_level": "yellow",
        "statement_md": d["statement_md"], "answer_sympy": None, "answer_md": d.get("answer_md") or d.get("correct"),
        "options": d.get("options"), "correct": d.get("correct"),
        "analysis": d.get("analysis", ""), "design_note": d.get("design_note", ""),
        "verification": {"blind_1": a1, "blind_2": a2, "raw1": raw1[:150], "raw2": raw2[:150]},
    }


# 已知弱区路由（Day3）：这些 kp 的参数化闭式已证实 LLM 写不稳（K1/矩阵域 0/7），
# 一律走确定性模板族（SymPy 从结构推导）。variant_of 变式请求不路由（保留 LLM 扰动路径供 M4 独立评测）。
_FAMILY_KP = {"罗尔定理": "rolle_xi", "拉格朗日中值定理": "lm_xi",
              "矩阵乘法": "matmul_entry", "逆矩阵": "inverse_2x2", "行列式": "det_3x3"}
_family_cycles: dict[str, object] = {}


def _next_family_spec(kp: str) -> dict | None:
    """按 kp 轮转返回下一个族实例（确定性序列：同调用序列 → 同实例序列 → 缓存可复演）。"""
    import itertools

    fam = _FAMILY_KP.get(kp)
    if not fam:
        return None
    if fam not in _family_cycles:
        specs = enumerate_family(fam, limit=8)
        if not specs:
            return None
        _family_cycles[fam] = itertools.cycle(specs)
    return next(_family_cycles[fam])


def _guard_statement(q: dict) -> dict:
    """空题干 guard（R5 数学语义终审 id18 教训：验证链只查答案未查题干非空）。
    statement_md 为空/空白的题一律转拦截 [待人工]，绝不下发——推题纪律的最后一道闸。"""
    if (q.get("statement_md") or "").strip():
        return q
    return {"kp": q.get("kp", ""), "difficulty": q.get("difficulty", ""), "qtype": q.get("qtype", ""),
            "verify_level": q.get("verify_level", "green"), "statement_md": None,
            "status": "blocked_pending_human",
            "error": q.get("error") or "题干为空（空题干 guard，R5 终审 id18）",
            "attempt": q.get("attempt", 1)}


def generate_question(kp_context: dict, difficulty: str = "基础", qtype: str = "calculation",
                      variant_of: str | None = None) -> dict:
    """生成一道题，带完整验证链：失败重生成 1 次 → 仍败 → 拦截 [待人工]（PRD §7）。
    重试使用独立缓存命名空间，确保第二次真正重新生成而非命中同一缓存。
    通过验证后追加一步「解析实例化」：以最终题干+答案为准重写解析，杜绝解析与实例脱节。
    已知弱区 kp（_FAMILY_KP）且非变式请求时，直接走确定性模板族（构造即正确）。
    qtype="solution"（Day6 任务 2）：解答题自评模式——题目内容走与计算题一致的绿标
    验证链（SymPy 构造 + 盲解对账，解析推导仍经验证），仅呈现方式改为不做机器判分、
    由学生对照标准解析三档自评（证明类无验证手段，仍不出）。"""
    if qtype in ("calculation", "solution") and variant_of is None:
        spec = _next_family_spec(str(kp_context.get("kp", "")))
        if spec is not None:
            q = generate_from_family(spec)
            if qtype == "solution":
                q["qtype"] = "solution"
            q["routed"] = "family"
            return _guard_statement(q)
    few_shot = pick_few_shot(kp_context.get("kp", ""), qtype)
    last_err = None
    for attempt in range(1, 3):
        try:
            if qtype == "calculation":
                q = _gen_green(kp_context, difficulty, few_shot, variant_of, attempt=attempt)
            elif qtype == "solution":
                q = _gen_green(kp_context, difficulty, few_shot, variant_of, attempt=attempt)
                q["qtype"] = "solution"
            else:
                q = _gen_yellow(kp_context, difficulty, qtype, few_shot, attempt=attempt)
            q["attempt"] = attempt
            q["analysis"] = _instantiate_analysis(q)
            return _guard_statement(q)
        except Exception as e:  # noqa: BLE001 —— 验证失败走重生成链
            last_err = e
    return {
        "kp": kp_context.get("kp", ""), "difficulty": difficulty, "qtype": qtype,
        "verify_level": "green" if qtype in ("calculation", "solution") else "yellow",
        "statement_md": None, "status": "blocked_pending_human",
        "error": str(last_err), "attempt": 2,
    }


def _kp_consistent(a: str, b: str) -> bool:
    """知识点一致性：相等或一方是另一方的子串（题库斜杠路径 kp vs 家族短名）。"""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _attach_variant(q: dict, src: dict) -> dict:
    """为变式题附加「源题与变化」溯源块（UI 直读；字段缺失也不崩）。"""
    new_params = q.get("params") or {}
    changed = {k: {"from": v, "to": new_params[k]}
               for k, v in (src.get("params") or {}).items()
               if k in new_params and str(v) != str(new_params[k])}
    introduced = {} if src.get("params") else dict(new_params)  # 源题无参数格（如题库题）时标注新引入参数
    _ORDER = {"基础": 0, "进阶": 1, "综合": 2}
    dims = []
    if changed or new_params:
        dims.append("参数扰动")
    if src.get("qtype") and q.get("qtype") and src["qtype"] != q["qtype"]:
        dims.append("题型转换")
    if _ORDER.get(q.get("difficulty", ""), 0) > _ORDER.get(src.get("difficulty", "基础"), 0):
        dims.append("难度上移")
    q["variant"] = {
        "source_kp": src.get("kp", ""),
        "source_statement_md": (src.get("statement_md") or "")[:240],
        "source_params": src.get("params") or {},
        "source_question_id": src.get("question_id"),
        "source_difficulty": src.get("difficulty") or "基础",
        "dimensions": dims or ["参数扰动"],
        "changed_params": changed,
        "introduced_params": introduced,
        "kp_consistent": _kp_consistent(src.get("kp", ""), q.get("kp", "")),
    }
    return q


def generate_variant(kp: str, source: dict) -> dict:
    """变式出题（Day6 任务 4）：以源错题为母本，出题并附「源题与变化」溯源块。

    - 家族 kp（含题库斜杠路径 kp 的子串匹配）：确定性族内换参数格（构造即正确，
      零 API 演示可用——用户能直接看出「换了哪些参数」）
    - 其余 kp：LLM 扰动路径（variant_of §三）；演示模式缓存未命中时走既有拦截链
      优雅降级（blocked 卡片仍带溯源块），绝不崩溃、绝不出网
    """
    src = {
        "kp": source.get("kp") or kp,
        "statement_md": (source.get("statement_md") or "")[:240],
        "params": source.get("params") or {},
        "question_id": source.get("question_id"),
        "difficulty": source.get("difficulty") or "基础",
        "qtype": source.get("qtype"),
    }
    key = (kp or src["kp"]).strip()
    fam = _FAMILY_KP.get(key) or next(
        (f for k, f in _FAMILY_KP.items() if k in key), None)
    if fam is not None:
        for spec in enumerate_family(fam, limit=16):
            if src["params"] and all(
                    str(spec["params"].get(k)) == str(v)
                    for k, v in src["params"].items() if k in spec["params"]):
                continue  # 与源题同参数格 → 不是变式，换下一实例
            q = generate_from_family(spec)
            return _guard_statement(_attach_variant(q, src))
    q = generate_question({"kp": kp or src["kp"]}, difficulty=src["difficulty"],
                          qtype="calculation", variant_of=src["statement_md"])
    return _attach_variant(q, src)


_ANALYSIS_SYSTEM = """你是数学题解析工程师。给定最终题干与标准答案，写出与该实例严格一致的解析：
3-5 句，含关键步骤、最终答案核对、以及该题的常见错误点破。所有数值必须与题干/答案一致，禁止出现题干以外的其他参数取值。只输出 JSON：{"analysis": "..."}"""


def _instantiate_analysis(q: dict) -> str:
    """以最终题干+答案重写解析（修复模板解析与实例化后的题干脱节）。"""
    if not q.get("statement_md"):
        return q.get("analysis", "")
    payload = f"题干：{q['statement_md']}\n标准答案：{q.get('answer_sympy') or q.get('answer_md')}"
    if q.get("options"):
        payload += f"\n选项：{q['options']}"
    try:
        d = chat_json([{"role": "system", "content": _ANALYSIS_SYSTEM},
                       {"role": "user", "content": payload}],
                      temperature=0.3, namespace="analysis:")
        return d.get("analysis") or q.get("analysis", "")
    except Exception:  # noqa: BLE001 —— 解析重写失败保留原解析
        return q.get("analysis", "")
