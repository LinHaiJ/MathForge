# 模块 J（伪考点闸门）测试：LLM 出题路径（绿标计算/黄标概念）的「考点装饰性问法」拦截。
#
# 覆盖：
#   demo 红线：MATHFORGE_DEMO=1 下 _gen_green/_gen_yellow 全部 messages（出题/盲解/
#     解析实例化）与改动前基线逐字节一致（基线 = 模块 J 动工前的 generate.py 常量与
#     消息构造逻辑，内联复制，做法同模块 F）；demo 盲解不含问法合规指令（无 kp_label）；
#     SKILL.md 内容 pin（demo 载荷含 SKILL.md 全文，动一字即打碎预热缓存，必须显式面对）
#   在线闸门：绿标盲解 FAKE_KP → attempt 2 重生成（命名空间独立，确认真的重新生成）
#     → 仍 FAKE_KP → blocked_pending_human 且 error 含「问法合规」人话原因
#   黄标双盲：双盲都 FAKE_KP → 问法合规拦截；单盲 FAKE_KP 单盲正常 → 双盲不一致
#     （既有失败语义覆盖，不特判放行）
#   后缀可见性：在线态出题 system prompt 含「考点装饰性问法❌」禁止条款；demo 态不含
#   不误伤：盲解正常作答时全链路照旧（绿标对账/复核仲裁/黄标双盲均通过）
#   家族路径不动：generate_from_family 盲解 system 在在线态仍与基线逐字节一致
#   namespace 代次：green v9 / yellow v6（ablation 臂同步 v2）
#   基线口径（模块 M 更新）：占位符契约换代改写的是 demo/在线共享的 _GREEN_SYSTEM
#   契约文本（绿标载荷变化由 gen-green v7→v9 代次隔离，演示预热重跑欠账待用户执行，
#   见 docs/2026-09-12_夜间优化/09 号）；本文件的「逐字节一致」基线 = 模块 M 换代后
#   的 generate.py 常量——demo 分支逻辑（后缀为空/kp_label=None）零改动仍被本文件守住。
#
# 零出网纪律：全部 LLM 调用打桩（假 chat_json）；在线态测试设置的 DEEPSEEK_API_KEY
# 是假 key，仅为过 _demo_mode 的 demo 门，真实网络调用不可能发生。

import hashlib
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import generate  # noqa: E402

# 固定输入（与基线对照共用；kp 用中文名，v2 侧传入 kp["name"] 的同一口径）
KP_CTX = {"kp": "泰勒公式", "definition_md": "泰勒公式：用多项式逼近光滑函数。",
          "key_formulas": ["f(x)=Σ f⁽ⁿ⁾(a)/n!·(x-a)ⁿ"], "common_mistakes": ["漏余项"]}
ANCHOR = "【真题锚 1】设 $f(x)=x^2$，求 $f'(x)$。\n答案：2x\n解析：幂函数求导。"
STATEMENT = "求 $f(x)=3x$ 在 $x=1$ 处的导数值。"
YELLOW_STATEMENT = "判断如下陈述是否正确：若函数在某点可导，则在该点连续。"

# 假 LLM 产物：params 单候选保证 random.choice 确定性；answer_expr 与盲解答案一致
GREEN_PRODUCT = {"statement_template": "求 $f(x)={a}x$ 在 $x=1$ 处的导数值。",
                 "params": {"a": [3]}, "answer_expr": "3",
                 "analysis": "对 $3x$ 求导得 3。", "design_note": "考察求导"}
YELLOW_PRODUCT = {"statement_md": YELLOW_STATEMENT, "answer_md": "正确",
                  "analysis": "可导必连续。", "design_note": "概念辨析"}


# --------------------------------------------------------------------------- #
# 基线（改动前 generate.py 逐字节内联复制；模块 J 动工前自活代码捕获）
# --------------------------------------------------------------------------- #
_GREEN_SYSTEM_BASELINE = '{skill}\n\n【本次任务：出 1 道🟢绿标计算题】额外输出契约（覆盖 §九 中的 answer 字段要求）：\n- "statement_template"：题干模板，参数用 <<name>> 占位（如 <<a>>；花括号一律留给 LaTeX\n  记号，禁止用花括号形态写参数——那是旧契约，会与 \\frac{{dy}}{{dx}} 之类记号冲突），\n  条件自含、单题单问。\n  参数只出现在数值位，且候选值应使代入后式子自然（系数位建议 2~5 的正整数，\n  系数 1 会触发渲染守卫被按确定错拦截；也避免代入后出现 "+ -3x" 或 "+ 0" 的丑形态）；\n  禁止在题干中出现"（xx 为常数）"之类元注释，常数项直接省略或自然融入\n- "params"：{{"参数名": [候选值1, 候选值2, ...]}}，全部是小整数或简分数，至少 2 个候选\n- "answer_expr"：SymPy 语法的标准答案表达式，可含 <<name>> 占位参数（如 "<<a>>**2/3+1"），\n  最终值由程序代入参数计算——你必须保证它数学上就是该题的正确答案\n【示范（answer_expr 必须真正随参数变化）】\n  statement_template: "求 $f(x)=<<a>>x^2+<<b>>x$ 在 $x=1$ 处的导数值。"\n  params: {{"a": [1,2,3], "b": [1,2]}}，answer_expr: "2*<<a>>+<<b>>"\n  反例（禁止）：中值定理"求 ξ"类写不出可靠参数化闭式时，改出导数值/简单积分/极限等直接计算题\n只输出 JSON。'

_YELLOW_SYSTEM_BASELINE = '{skill}\n\n【本次任务：出 1 道🟡黄标{qtype_label}】答案必须是可规范化短串（独立解题者只凭题面作答，必须可判卷）：\n- 概念判断→题面必须是"判断如下陈述是否正确：…"，答案只有 正确 或 错误（陈述要客观、无歧义、教科书可判）\n- 排序→题面给出带标号的步骤，答案如 B→A→C（用题面标号体系，唯一确定）\n- 单项选择→恰好 4 个选项，答案为单个字母\n只输出 JSON（按 §九 契约，含 options/correct 若为选择题）。'

_SOLVE_SYSTEM_BASELINE = '你是独立的考研数学解题者。只根据题面独立作答，禁止臆测出题人意图。\n作答前先检查题面前提是否自洽（例：罗尔定理要求端点函数值相等、所求点必须落在给定开区间内；\n参数化题目代入具体值后条件是否仍成立）。若发现前提自相矛盾或无解，输出 {"answer": "PREMISE_BROKEN"}。\n选择→只输出字母；排序→只输出如 A→B→C（用题面标号体系，输出完整序列）；\n判断→只输出 正确 或 错误；计算→只输出最终结果的 SymPy 表达式\n（常数用 E/pi、分数用 Rational(a,b)、根号用 sqrt()、自然对数必须写 log() 禁用 ln）。\n只输出 JSON：{"answer": "<规范化短串>"}'

_ANALYSIS_SYSTEM_BASELINE = '你是数学题解析工程师。给定最终题干与标准答案，写出与该实例严格一致的解析：\n3-5 句，含关键步骤、最终答案核对、以及该题的常见错误点破。所有数值必须与题干/答案一致，禁止出现题干以外的其他参数取值。只输出 JSON：{"analysis": "..."}'

_FEWSHOT_HEADER_BASELINE = "\n\n【真题风格锚（模仿其表述与难度，禁止抄题）】\n"

_SKILL_MD_SHA256_BASELINE = "48fe7f5485fea488c9af1afe9ace6c86a1885fcfa8d31acc96592a66aead2404"


def _base_user_baseline(kp_context: dict, difficulty: str) -> str:
    """generate._base_user 逻辑内联复制——含模块 F3 的 memory_context 追加分支
    （本模块未改该函数，纯防御；KP_CTX 未含 memory_context 故两侧输出一致，
    记忆注入的不变式由 test_memory_inject 独立把守）。若未来 _base_user 变化，
    本基线须同步更新以保持字节级对照有效。"""
    parts = [
        f"知识点：{kp_context.get('kp', kp_context.get('name', ''))}",
        f"定义：{kp_context.get('definition_md', '')}",
    ]
    if kp_context.get("key_formulas"):
        parts.append("关键公式：" + "；".join(kp_context["key_formulas"][:4]))
    if kp_context.get("common_mistakes"):
        parts.append("常见错误（可作陷阱素材）：" + "；".join(kp_context["common_mistakes"][:3]))
    parts.append(f"难度目标：{difficulty}")
    memory = kp_context.get("memory_context")
    if isinstance(memory, str) and memory.strip():
        parts.append(memory.strip())
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 假 LLM（零出网）：按 namespace 分发并记录 (namespace, messages 深拷贝)
# --------------------------------------------------------------------------- #
def _snap(captured, solve_answer="3"):
    def fake(messages, **kw):
        ns = kw.get("namespace", "")
        captured.append((ns, json.loads(json.dumps(messages, ensure_ascii=False))))
        if ns.startswith("gen-green"):
            return json.loads(json.dumps(GREEN_PRODUCT))
        if ns.startswith("gen-yellow"):
            return json.loads(json.dumps(YELLOW_PRODUCT))
        if ns.startswith("solve"):
            return {"answer": solve_answer}
        if ns.startswith("analysis"):
            return {"analysis": "解析。"}
        return {}
    return fake


def _snap_solve_seq(captured, seq, fallback="正确"):
    """solve 答案按序列轮转（单盲 FAKE_KP / 复核仲裁场景）；序列耗尽回落 fallback。"""
    it = iter(seq)

    def fake(messages, **kw):
        ns = kw.get("namespace", "")
        captured.append((ns, json.loads(json.dumps(messages, ensure_ascii=False))))
        if ns.startswith("gen-green"):
            return json.loads(json.dumps(GREEN_PRODUCT))
        if ns.startswith("gen-yellow"):
            return json.loads(json.dumps(YELLOW_PRODUCT))
        if ns.startswith("solve"):
            try:
                return {"answer": next(it)}
            except StopIteration:
                return {"answer": fallback}
        if ns.startswith("analysis"):
            return {"analysis": "解析。"}
        return {}
    return fake


def _online(monkeypatch):
    """离开 demo 门（generate._demo_mode 需 DEMO=0 且有 key）；LLM 已打桩零出网。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key-gate-tests")


# --------------------------------------------------------------------------- #
# demo 红线：载荷逐字节不变
# --------------------------------------------------------------------------- #
def test_demo_skill_md_unchanged_guard():
    """SKILL.md 内容 pin：demo 态 system prompt 含 SKILL.md 全文（load_skill），
    任何 SKILL.md 改动都会使 demo 预热缓存全量失效——改动者必须先面对这一事实
    （重预热 + 更新本 pin + 命名空间代次评估），而不是静默打碎演示。"""
    assert hashlib.sha256(generate.load_skill().encode()).hexdigest() == _SKILL_MD_SHA256_BASELINE


def test_demo_green_payload_byte_identical(monkeypatch):
    """demo 下 _gen_green 的出题与盲解 messages 与改动前基线逐字节一致。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "1")  # conftest 已密封，显式重申
    captured = []
    monkeypatch.setattr(generate, "chat_json", _snap(captured))
    q = generate._gen_green(dict(KP_CTX), "基础", [ANCHOR], attempt=1)
    assert q["statement_md"] == STATEMENT
    baseline_gen_user = _base_user_baseline(KP_CTX, "基础") + _FEWSHOT_HEADER_BASELINE + ANCHOR
    expected = [
        ("gen-green:v9:a1:",
         [{"role": "system", "content": _GREEN_SYSTEM_BASELINE.format(skill=generate.load_skill())},
          {"role": "user", "content": baseline_gen_user}]),
        ("solve:",
         [{"role": "system", "content": _SOLVE_SYSTEM_BASELINE},
          {"role": "user", "content": STATEMENT}]),
    ]
    assert captured == expected
    # demo 盲解不被追问 FAKE_KP：system 无问法合规指令、无 FAKE_KP 字样
    assert "问法合规检查" not in captured[1][1][0]["content"]
    assert "FAKE_KP" not in captured[1][1][0]["content"]
    # 解析实例化调用同样逐字节不变
    captured.clear()
    generate._instantiate_analysis(q)
    assert captured == [
        ("analysis:",
         [{"role": "system", "content": _ANALYSIS_SYSTEM_BASELINE},
          {"role": "user", "content": f"题干：{STATEMENT}\n标准答案：3"}]),
    ]


def test_demo_yellow_payload_byte_identical(monkeypatch):
    """demo 下 _gen_yellow 的出题与双盲 messages 与改动前基线逐字节一致。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    captured = []
    monkeypatch.setattr(generate, "chat_json", _snap(captured, solve_answer="正确"))
    q = generate._gen_yellow(dict(KP_CTX), "基础", "concept", [ANCHOR], attempt=1)
    assert q["statement_md"] == YELLOW_STATEMENT
    baseline_gen_user = _base_user_baseline(KP_CTX, "基础") + _FEWSHOT_HEADER_BASELINE + ANCHOR
    solve_msgs = [{"role": "system", "content": _SOLVE_SYSTEM_BASELINE},
                  {"role": "user", "content": YELLOW_STATEMENT}]
    expected = [
        ("gen-yellow:v6:a1:",
         [{"role": "system",
           "content": _YELLOW_SYSTEM_BASELINE.format(skill=generate.load_skill(),
                                                     qtype_label="概念判断")},
          {"role": "user", "content": baseline_gen_user}]),
        ("solve:", solve_msgs),   # 既有行为：双盲两次同参调用（同缓存键）
        ("solve:", solve_msgs),
    ]
    assert captured == expected
    assert all("问法合规检查" not in m["content"] for _, msgs in captured for m in msgs)
    captured.clear()
    generate._instantiate_analysis(q)
    assert captured == [
        ("analysis:",
         [{"role": "system", "content": _ANALYSIS_SYSTEM_BASELINE},
          {"role": "user", "content": f"题干：{YELLOW_STATEMENT}\n标准答案：正确"}]),
    ]


def test_demo_full_chain_no_gate_markers(monkeypatch):
    """demo 全链路（generate_question）：正常出题不被闸门误伤，任何载荷无闸门痕迹。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    captured = []
    monkeypatch.setattr(generate, "chat_json", _snap(captured))
    q = generate.generate_question(dict(KP_CTX), difficulty="基础", qtype="calculation")
    assert q.get("status") != "blocked_pending_human"
    assert q["verification"]["solver_agree"] is True
    assert all("问法合规检查" not in m["content"] and "考点装饰性问法" not in m["content"]
               for _, msgs in captured for m in msgs)


# --------------------------------------------------------------------------- #
# 在线闸门：绿标
# --------------------------------------------------------------------------- #
def test_online_green_fake_kp_regen_then_blocked(monkeypatch):
    """绿标盲解 FAKE_KP → attempt 2 重生成 → 仍 FAKE_KP → 拦截且 error 为人话原因。"""
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(generate, "chat_json", _snap(captured, solve_answer="FAKE_KP"))
    q = generate.generate_question(dict(KP_CTX), difficulty="基础", qtype="calculation")
    assert q["status"] == "blocked_pending_human"
    assert "问法合规" in q["error"] and "考点未承重" in q["error"]
    assert q["attempt"] == 2
    ns_list = [ns for ns, _ in captured]
    # attempt 缓存命名空间独立：两次生成 messages 相同但缓存键不同 → 重试真的重新生成
    assert ns_list.count("gen-green:v9:a1:") == 1
    assert ns_list.count("gen-green:v9:a2:") == 1
    m1 = next(m for ns, m in captured if ns == "gen-green:v9:a1:")
    m2 = next(m for ns, m in captured if ns == "gen-green:v9:a2:")
    assert m1 == m2  # 两次生成的 prompt 相同——重试靠独立命名空间换缓存键，而非改写 prompt
    # 盲解被注入问法合规检查（含考点中文名与 FAKE_KP 输出约定）；FAKE_KP 是判定性
    # 结论，不做 verify_mode 复审 → 每次尝试恰好一次盲解
    solve_msgs = [m for ns, m in captured if ns == "solve:"]
    assert len(solve_msgs) == 2
    for msgs in solve_msgs:
        assert "问法合规检查" in msgs[0]["content"]
        assert "泰勒公式" in msgs[0]["content"]
        assert "FAKE_KP" in msgs[0]["content"]
    # 出题 system prompt 含在线态禁止条款
    for ns, msgs in captured:
        if ns.startswith("gen-green"):
            assert "考点装饰性问法" in msgs[0]["content"]


def test_online_green_normal_answer_unaffected(monkeypatch):
    """FAKE_KP 不误伤：盲解正常作答 → 绿标对账照旧通过（答案对错仍由 SymPy 终审）。"""
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(generate, "chat_json", _snap(captured, solve_answer="3"))
    q = generate.generate_question(dict(KP_CTX), difficulty="基础", qtype="calculation")
    assert q.get("status") != "blocked_pending_human"
    assert q["statement_md"] == STATEMENT
    assert q["verification"]["solver_agree"] is True
    assert q["attempt"] == 1


def test_online_green_verify_arbitration_still_works(monkeypatch):
    """在线态复核仲裁不回归：首盲偶发算错 → verify_mode 复核解与 SymPy 一致 → 放行。"""
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(generate, "chat_json", _snap_solve_seq(captured, ["999", "3"]))
    q = generate.generate_question(dict(KP_CTX), difficulty="基础", qtype="calculation")
    assert q.get("status") != "blocked_pending_human"
    assert q["verification"]["solver_agree"] is True
    ns_list = [ns for ns, _ in captured]
    assert ns_list.count("solve:") == 2  # 首盲 + 复核


# --------------------------------------------------------------------------- #
# 在线闸门：黄标双盲
# --------------------------------------------------------------------------- #
def test_online_yellow_both_blind_fake_kp_blocked(monkeypatch):
    """黄标双盲都 FAKE_KP → 问法合规拦截（attempt 2 仍败 → blocked）。"""
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(generate, "chat_json", _snap(captured, solve_answer="FAKE_KP"))
    q = generate.generate_question(dict(KP_CTX), difficulty="基础", qtype="concept")
    assert q["status"] == "blocked_pending_human"
    assert "问法合规" in q["error"] and "考点未承重" in q["error"]
    assert q["attempt"] == 2
    ns_list = [ns for ns, _ in captured]
    assert "gen-yellow:v6:a1:" in ns_list and "gen-yellow:v6:a2:" in ns_list
    for ns, msgs in captured:
        if ns.startswith("gen-yellow"):
            assert "考点装饰性问法" in msgs[0]["content"]


def test_online_yellow_single_fake_kp_is_mismatch_not_gate(monkeypatch):
    """单盲 FAKE_KP 单盲正常 → 双盲不一致（既有失败语义覆盖，不按问法合规特判）；
    error 追加值班提示（对抗审查 minor-4：不误归因为答案抖动）。"""
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(generate, "chat_json",
                        _snap_solve_seq(captured, ["FAKE_KP", "正确"] * 4))
    q = generate.generate_question(dict(KP_CTX), difficulty="基础", qtype="concept")
    assert q["status"] == "blocked_pending_human"
    assert "双盲不一致" in q["error"]
    assert "建议人工复核" in q["error"]
    assert "考点未承重" not in q["error"]  # 未按问法合规闸门特判拦截（既有不一致语义）


def test_online_yellow_plain_mismatch_has_no_review_hint(monkeypatch):
    """普通双盲不一致（无 FAKE_KP 参与）不带人工复核提示——提示只跟问法合规分歧走。"""
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(generate, "chat_json",
                        _snap_solve_seq(captured, ["错误", "正确"] * 4, fallback="错误"))
    q = generate.generate_question(dict(KP_CTX), difficulty="基础", qtype="concept")
    assert q["status"] == "blocked_pending_human"
    assert "双盲不一致" in q["error"]
    assert "建议人工复核" not in q["error"]


def test_online_yellow_normal_answer_unaffected(monkeypatch):
    """黄标双盲正常作答 → 照旧通过。"""
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(generate, "chat_json", _snap(captured, solve_answer="正确"))
    q = generate.generate_question(dict(KP_CTX), difficulty="基础", qtype="concept")
    assert q.get("status") != "blocked_pending_human"
    assert q["verification"]["blind_1"] == "正确"
    assert q["verification"]["blind_2"] == "正确"


# --------------------------------------------------------------------------- #
# 后缀可见性与家族路径不动
# --------------------------------------------------------------------------- #
def test_online_system_suffix_demo_absent(monkeypatch):
    """在线态出题 system prompt 含禁止条款后缀；demo 态不含（字节级基线已证，此处显式）。"""
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(generate, "chat_json", _snap(captured))
    generate._gen_green(dict(KP_CTX), "基础", [ANCHOR], attempt=1)
    assert "考点装饰性问法" in captured[0][1][0]["content"]
    captured.clear()
    monkeypatch.setattr(generate, "chat_json", _snap(captured, solve_answer="正确"))
    generate._gen_yellow(dict(KP_CTX), "基础", "concept", [ANCHOR], attempt=1)
    assert "考点装饰性问法" in captured[0][1][0]["content"]
    # demo 侧
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    captured.clear()
    monkeypatch.setattr(generate, "chat_json", _snap(captured))
    generate._gen_green(dict(KP_CTX), "基础", [ANCHOR], attempt=1)
    assert "考点装饰性问法" not in captured[0][1][0]["content"]


def test_family_blind_solve_never_gets_gate_online(monkeypatch):
    """家族路径（零 API 演示路径）不加闸门：在线态下家族盲解 system 仍与基线逐字节一致。"""
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(generate, "chat_json", _snap(captured, solve_answer="3"))
    q = generate.generate_question({"kp": "罗尔定理"}, difficulty="基础", qtype="calculation")
    assert q.get("routed") == "family"
    ns_list = [ns for ns, _ in captured]
    assert not any(ns.startswith("gen-green") or ns.startswith("gen-yellow") for ns in ns_list)
    solve_msgs = [m for ns, m in captured if ns == "solve:"]
    assert solve_msgs
    for msgs in solve_msgs:  # verify_mode 只改 user 侧，system 恒为基线
        assert msgs[0]["content"] == _SOLVE_SYSTEM_BASELINE
        assert "问法合规检查" not in msgs[0]["content"]


# --------------------------------------------------------------------------- #
# 指令文案：判据显式二分（对抗审查 major-1）
# --------------------------------------------------------------------------- #
def test_fake_kp_instruction_dichotomy_examples():
    """盲解指令与出题后缀必须显式二分：计算型考点（核心运算即解题运算）是合规侧
    （泰勒求极限/渐近线/二重积分/微分近似不得因「直接计算可解」被误伤），
    FAKE 只留给「考点名仅是标签」的装饰性问法（罗尔/拉格朗日反例锚定）。"""
    ins = generate._FAKE_KP_INSTRUCTION
    for kw in ("泰勒", "渐近线", "二重积分", "微分近似",  # 合规侧正例
               "合规，正常作答",                          # 合规侧出路
               "遮住考点名", "罗尔", "拉格朗日",          # 伪考点侧反例
               "FAKE_KP", "拿不准时按合规处理"):          # FAKE 约定 + 豁免条款
        assert kw in ins, f"盲解指令缺关键词：{kw}"
    suffix = generate._BAN_FAKE_KP_SUFFIX
    for kw in ("考点装饰性问法", "泰勒", "渐近线", "二重积分", "合规", "罗尔"):
        assert kw in suffix, f"出题后缀缺关键词：{kw}"


# --------------------------------------------------------------------------- #
# namespace 代次
# --------------------------------------------------------------------------- #
def test_namespace_generations_bumped():
    """green v9（模块 M 占位符契约换代+系数位契约对齐）/ yellow v6（模块 J 伪考点闸门+SKILL 禁止清单
    代次，黄标无参数化模板不随 M 变）；ablation 臂同步 v2。"""
    spec = importlib.util.spec_from_file_location(
        "generate_fresh_j", os.path.join(ROOT, "generate.py"))
    fresh = importlib.util.module_from_spec(spec)
    ablation = os.environ.get("MATHFORGE_ABLATION")
    if ablation:
        del os.environ["MATHFORGE_ABLATION"]  # fresh 实例按默认（非 ablation）分支断言
    try:
        spec.loader.exec_module(fresh)
    finally:
        if ablation:
            os.environ["MATHFORGE_ABLATION"] = ablation
    assert fresh._NS_GREEN == "gen-green:v9:a{}:"
    assert fresh._NS_YELLOW == "gen-yellow:v6:a{}:"
    assert generate._NS_GREEN.startswith("gen-green-ab:v2") if generate._ABLATION \
        else generate._NS_GREEN == "gen-green:v9:a{}:"
    assert generate._NS_YELLOW.startswith("gen-yellow-ab:v2") if generate._ABLATION \
        else generate._NS_YELLOW == "gen-yellow:v6:a{}:"
