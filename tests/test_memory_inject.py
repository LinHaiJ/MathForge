# 模块 F（记忆×出题闭环）测试：召回（F2）→ 渲染 → 注入（F3）→ HTTP 集成（F1/F3）。
#
# 覆盖：
#   F2  mem2.recall_for_generation：无事件→None / 错题计数 / override 优先 /
#       最近错题含 meta.q·ans / 旧事件无 meta.q 不崩 / kp 隔离 / 窗口 8 / 纯只读
#   F3  generate._base_user：无 memory_context 时输出与改动前逐字节一致
#       （基线 = git HEAD 逻辑内联复制）；非空时记忆块追加到末尾
#   F1  /v2/answer、/v2/selfassess 事件 meta 落 q/ans 键
#   F3  /v2/turn 集成：答错→再出题，假 LLM 捕获 prompt 含「错因记忆」+真实错因类型；
#       空学生 prompt 不含该块；demo 模式零 API 链路不破
# 隔离纪律：mem2 全走 tmp_path 临时库；HTTP 用 MATHFORGE_V2_DB/MATHFORGE_PROFILES_DIR
#   指向临时目录——真实 mathforge.db / profiles/ 全程不写。

import json
import os
import sqlite3
import sys
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import generate  # noqa: E402
import mem2  # noqa: E402

KP = "calc.limit.basic"          # 无包内族、非 root 家族 → /v2/turn 必走 LLM 路径
FAMILY_KP = "calc.rolle"         # 包内族 kp → demo 零 API 可出题


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _conn(tmp_path):
    p = tmp_path / f"{uuid.uuid4().hex}.db"
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    mem2.init_schema(conn)
    return conn


def _ev(conn, kp=KP, result="wrong", mode="answer", ts=1000.0, pack="calculus",
        attribution=None, user_override=None, meta=None):
    return mem2.append_event(
        conn, pack=pack, kp=kp, qtype="fill", mode=mode, result=result,
        attribution=attribution, user_override=user_override, meta=meta, ts=ts)


def _base_user_baseline(kp_context: dict, difficulty: str) -> str:
    """改动前（git HEAD）的 generate._base_user 逻辑内联复制——无记忆不变式的字节级基准。"""
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


def _make_client(monkeypatch, tmp_path):
    """每测试独立临时库 + 档案目录（真实库/profiles 零接触）。"""
    monkeypatch.setenv("MATHFORGE_V2_DB", str(tmp_path / "inject.db"))
    monkeypatch.setenv("MATHFORGE_PROFILES_DIR", str(tmp_path / "profiles"))
    from fastapi.testclient import TestClient
    import app as app_mod
    return TestClient(app_mod.app)


def _fake_chat_json(captured):
    """假 LLM：按 namespace 分发。gen-green 捕获 user prompt 并返回可过全验证链的绿标产物；
    solve（盲解）/ analysis（解析实例化）返回与构造一致的结果——判分权仍在 SymPy 链。"""

    def fake(messages, **kw):
        ns = kw.get("namespace", "")
        if ns.startswith("gen-green"):
            captured.append(messages[-1]["content"])
            return {"statement_template": "求 $f(x)=x$ 在 $x=1$ 处的导数值。",
                    "params": {"a": [1, 2]}, "answer_expr": "1"}
        if ns.startswith("solve"):
            return {"answer": "1"}
        if ns.startswith("analysis"):
            return {"analysis": "对 $f(x)=x$ 求导得 1，与标准答案一致。"}
        return {}

    return fake


def _last_event_meta(db_path, mode):
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT meta FROM attempt_events WHERE mode=? ORDER BY id DESC LIMIT 1",
            (mode,)).fetchone()
        return json.loads(row[0]) if row and row[0] else {}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# F2：recall_for_generation
# --------------------------------------------------------------------------- #
def test_recall_no_events_returns_none(tmp_path):
    conn = _conn(tmp_path)
    assert mem2.recall_for_generation(conn, KP) is None
    conn.close()


def test_recall_counts_and_recent_wrongs_with_meta_q_ans(tmp_path):
    conn = _conn(tmp_path)
    _ev(conn, result="correct", ts=1000.0)
    _ev(conn, result="wrong", ts=2000.0,
        attribution={"type": "计算失误", "conf": 0.9}, meta={"q": "题面A", "ans": "学生答A"})
    _ev(conn, result="wrong", ts=3000.0,
        attribution={"type": "概念混淆", "conf": 0.8}, meta={"q": "题面B", "ans": "学生答B"})
    r = mem2.recall_for_generation(conn, KP)
    assert r is not None
    assert r["n"] == 3 and r["wrong"] == 2
    assert r["attribution_counts"] == {"计算失误": 1, "概念混淆": 1}
    # 最近错题：新→旧；meta 有 q/ans 就带上
    assert [w["q"] for w in r["recent_wrongs"]] == ["题面B", "题面A"]
    assert [w["ans"] for w in r["recent_wrongs"]] == ["学生答B", "学生答A"]
    assert r["recent_wrongs"][0]["attribution_type"] == "概念混淆"
    assert r["recent_wrongs"][0]["day"]
    conn.close()


def test_recall_override_event_priority(tmp_path):
    """独立 override 事件 > 事件 attribution（复用 _resolve_attribution 口径）。"""
    conn = _conn(tmp_path)
    eid = _ev(conn, result="wrong", ts=2000.0,
              attribution={"type": "概念混淆", "conf": 0.9}, meta={"q": "题面", "ans": "答"})
    mem2.override_attribution(conn, eid, "审题错误")
    r = mem2.recall_for_generation(conn, KP)
    assert r["n"] == 1  # override/retry 事件不进召回窗口
    assert r["attribution_counts"] == {"审题错误": 1}
    assert r["recent_wrongs"][0]["attribution_type"] == "审题错误"
    conn.close()


def test_recall_event_level_user_override_priority(tmp_path):
    """事件自身 user_override > attribution（_resolve_attribution 优先级口径）。"""
    conn = _conn(tmp_path)
    _ev(conn, result="wrong", ts=2000.0,
        attribution={"type": "概念混淆", "conf": 0.9},
        user_override={"type": "方法选错", "conf": 1.0})
    r = mem2.recall_for_generation(conn, KP)
    assert r["attribution_counts"] == {"方法选错": 1}
    conn.close()


def test_recall_placeholder_attribution_not_counted(tmp_path):
    """「未归因」占位（attribute.py 归因失败兜底）与 None 同义：
    不进 attribution_counts；摘录行仍标注「未归因」；counts 空 → 指令降级。"""
    conn = _conn(tmp_path)
    _ev(conn, result="wrong", ts=1000.0,
        attribution={"type": "未归因", "conf": 0.0}, meta={"q": "题A"})
    _ev(conn, result="wrong", ts=2000.0, attribution=None, meta={"q": "题B"})
    r = mem2.recall_for_generation(conn, KP)
    assert r["wrong"] == 2
    assert r["attribution_counts"] == {}                       # 占位/None 都不算真实错因
    assert all(w["attribution_type"] is None for w in r["recent_wrongs"])
    block = mem2.format_memory_block(r)
    assert "高频错因" not in block                              # 无悬空指代
    assert "【1970-01-01|未归因】" in block                     # 摘录标注未归因
    assert "针对最近错题摘录的错因设计陷阱与干扰项" in block      # 指令降级文案
    assert "针对高频错因设计陷阱与干扰项" not in block
    conn.close()


def test_recall_excludes_event_from_excerpts_but_keeps_counts(tmp_path):
    """exclude_event_ids（变式场景剔除源题事件）：摘录不再含源题题面，
    但 n/wrong/attribution_counts 仍计入（归因统计不含题面，无重复注入问题）。"""
    conn = _conn(tmp_path)
    id_old = _ev(conn, result="wrong", ts=1000.0,
                 attribution={"type": "计算失误", "conf": 0.9}, meta={"q": "旧题面"})
    id_src = _ev(conn, result="wrong", ts=2000.0,
                 attribution={"type": "概念混淆", "conf": 0.9}, meta={"q": "源题题面"})
    r = mem2.recall_for_generation(conn, KP, exclude_event_ids={id_src})
    assert r["n"] == 2 and r["wrong"] == 2
    assert r["attribution_counts"] == {"计算失误": 1, "概念混淆": 1}
    assert [w["q"] for w in r["recent_wrongs"]] == ["旧题面"]   # 源题摘录被剔除
    # 不剔除时两条摘录都在（回归对照）
    r_full = mem2.recall_for_generation(conn, KP)
    assert [w["q"] for w in r_full["recent_wrongs"]] == ["源题题面", "旧题面"]
    assert id_old and id_src
    conn.close()


def test_recall_legacy_events_without_meta_q_do_not_crash(tmp_path):
    """旧事件 meta 无 q/ans 键（甚至 meta=None）→ 缺省省略，绝不崩。"""
    conn = _conn(tmp_path)
    _ev(conn, result="wrong", ts=2000.0,
        attribution={"type": "计算失误", "conf": 0.9}, meta=None)
    _ev(conn, result="wrong", ts=3000.0,
        attribution={"type": "计算失误", "conf": 0.9},
        meta={"stmt_summary": "旧事件只有摘要键"})  # 无 q/ans
    r = mem2.recall_for_generation(conn, KP)
    assert r is not None and r["wrong"] == 2
    assert r["attribution_counts"] == {"计算失误": 2}
    for w in r["recent_wrongs"]:
        assert "q" not in w and "ans" not in w
    conn.close()


def test_recall_kp_scoped_window_capped_limit3(tmp_path):
    conn = _conn(tmp_path)
    for i in range(10):
        _ev(conn, result="wrong", ts=1000.0 + i,
            attribution={"type": "计算失误", "conf": 1.0}, meta={"q": f"题{i}"})
    _ev(conn, kp="calc.rolle", result="wrong", ts=5000.0)  # 其他 kp 不串
    r = mem2.recall_for_generation(conn, KP)
    assert r["n"] == 8 and r["wrong"] == 8          # 默认窗口 8
    assert len(r["recent_wrongs"]) == 3             # 默认 limit=3
    assert [w["q"] for w in r["recent_wrongs"]] == ["题9", "题8", "题7"]
    other = mem2.recall_for_generation(conn, "calc.rolle")
    assert other is not None and other["n"] == 1 and other["wrong"] == 1
    conn.close()


def test_recall_selfassess_partial_counts_as_mistake_retry_excluded(tmp_path):
    """错题口径与 project_mistakes 一致（selfassess+partial 算错题）；retry 不进窗口。"""
    conn = _conn(tmp_path)
    _ev(conn, mode="selfassess", result="partial", ts=2000.0,
        attribution={"type": "方法选错", "conf": 1.0})
    _ev(conn, result="wrong", ts=1000.0, attribution={"type": "计算失误", "conf": 1.0})
    _ev(conn, result="correct", mode="retry", ts=3000.0, meta={"retry": True})
    r = mem2.recall_for_generation(conn, KP)
    assert r["n"] == 2 and r["wrong"] == 2
    assert r["attribution_counts"] == {"方法选错": 1, "计算失误": 1}
    conn.close()


def test_recall_pattern_card_from_event_pack(tmp_path):
    conn = _conn(tmp_path)
    _ev(conn, result="wrong", ts=2000.0, pack="calculus",
        attribution={"type": "计算失误", "conf": 1.0})
    mem2.patterns_set(conn, "calculus", KP, "该生极限计算常丢等价无穷小代换条件",
                      source="rule")
    r = mem2.recall_for_generation(conn, KP)
    assert r["pattern_card"] == "该生极限计算常丢等价无穷小代换条件"
    conn.close()


def test_recall_is_pure_readonly(tmp_path):
    """纯只读红线：召回前后 conn.total_changes 不变（无任何写入）。"""
    conn = _conn(tmp_path)
    _ev(conn, result="wrong", ts=2000.0, attribution={"type": "计算失误", "conf": 1.0})
    mem2.override_attribution(conn, 1, "审题错误")
    before = conn.total_changes
    assert mem2.recall_for_generation(conn, KP) is not None
    assert conn.total_changes == before
    conn.close()


# --------------------------------------------------------------------------- #
# F3：format_memory_block（渲染）
# --------------------------------------------------------------------------- #
def test_format_block_contains_header_counts_redline(tmp_path):
    conn = _conn(tmp_path)
    _ev(conn, result="wrong", ts=1000.0,
        attribution={"type": "计算失误", "conf": 0.9}, meta={"q": "题A", "ans": "答A"})
    _ev(conn, result="wrong", ts=2000.0,
        attribution={"type": "计算失误", "conf": 0.9}, meta={"q": "题B", "ans": "答B"})
    _ev(conn, result="wrong", ts=3000.0, attribution={"type": "概念混淆", "conf": 0.8})
    block = mem2.format_memory_block(mem2.recall_for_generation(conn, KP))
    assert block.startswith("【学生个人错因记忆（供出题参考，严禁在题干中提及或复述）】")
    assert "错 3 次" in block
    assert "计算失误×2" in block and "概念混淆×1" in block
    assert "【1970-01-01|概念混淆】" in block          # 最近一条排最前
    assert "题干摘录：题B" in block and "学生答：答B" in block
    # 指令红线（写进块内）
    assert "针对高频错因设计陷阱与干扰项" in block
    assert "变式题优先针对最近一次错因做扰动" in block
    assert "绝不把学生历史写进题干" in block
    conn.close()


def test_format_block_empty_when_no_wrong_or_none(tmp_path):
    """无错题历史/无事件 → ""：调用侧不注入，prompt 与无记忆版逐字节一致。"""
    conn = _conn(tmp_path)
    _ev(conn, result="correct", ts=1000.0)
    r = mem2.recall_for_generation(conn, KP)
    assert r is not None and r["wrong"] == 0
    assert mem2.format_memory_block(r) == ""
    assert mem2.format_memory_block(None) == ""
    assert mem2.format_memory_block({}) == ""
    conn.close()


def test_full_chain_recall_format_inject(tmp_path):
    """召回→渲染→_base_user 注入全链：块内含真实错因类型与错题摘录。"""
    conn = _conn(tmp_path)
    _ev(conn, result="wrong", ts=2000.0,
        attribution={"type": "计算失误", "conf": 0.9},
        meta={"q": "求极限题面", "ans": "0"})
    block = mem2.format_memory_block(mem2.recall_for_generation(conn, KP))
    assert "计算失误" in block
    out = generate._base_user({"kp": "极限计算基础", "memory_context": block}, "基础")
    assert "学生个人错因记忆" in out and "计算失误" in out
    assert "求极限题面" in out and "学生答：0" in out
    conn.close()


# --------------------------------------------------------------------------- #
# F3 不变式：无记忆时 _base_user 与改动前逐字节一致
# --------------------------------------------------------------------------- #
_BASE_CTXS = [
    {"kp": "罗尔定理"},
    {"kp": "极限计算基础", "definition_md": "定义文本",
     "key_formulas": ["公式一", "公式二"], "common_mistakes": ["坑一", "坑二"]},
    {"name": "仅名称回退"},
    {"kp": "x", "key_formulas": [], "common_mistakes": []},
]


@pytest.mark.parametrize("difficulty", ["基础", "进阶"])
@pytest.mark.parametrize("ctx", _BASE_CTXS)
def test_base_user_without_memory_byte_identical_to_baseline(ctx, difficulty):
    """无 memory_context（含空串/纯空白/None/非字符串脏值）时输出与基线逐字节一致。"""
    assert generate._base_user(ctx, difficulty) == _base_user_baseline(ctx, difficulty)
    for empty in ("", "   ", "\n", None, 123, {"dirty": 1}):
        assert generate._base_user({**ctx, "memory_context": empty}, difficulty) == \
            _base_user_baseline(ctx, difficulty)


@pytest.mark.parametrize("difficulty", ["基础", "综合"])
def test_base_user_with_memory_appends_block_at_end(difficulty):
    """非空记忆块 → 原输出末尾原样追加（基线 + "\\n" + 块），块内容不被改写。"""
    block = ("【学生个人错因记忆（供出题参考，严禁在题干中提及或复述）】\n"
             "- 该生近 2 次作答：错 2 次；高频错因：计算失误×2\n"
             "- 出题指令：针对高频错因设计陷阱与干扰项。")
    ctx = {"kp": "罗尔定理", "definition_md": "定义", "memory_context": block}
    out = generate._base_user(ctx, difficulty)
    assert out == _base_user_baseline({"kp": "罗尔定理", "definition_md": "定义"},
                                      difficulty) + "\n" + block
    assert "计算失误" in out


# --------------------------------------------------------------------------- #
# F1：作答落库 meta 补 q/ans（HTTP，临时库）
# --------------------------------------------------------------------------- #
def test_answer_event_meta_records_q_ans(monkeypatch, tmp_path):
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    cli = _make_client(monkeypatch, tmp_path)
    stmt = "求 $\\lim_{x\\to0}\\frac{\\sin x}{x}$。"
    r = cli.post("/v2/answer", json={
        "pack_id": "calculus", "kp": KP, "qtype": "fill",
        "student_answer": "0", "standard_answer": "1",
        "statement_md": stmt, "difficulty": "基础",
        "attribution_override": "计算失误",
    }).json()
    assert r["ok"] and r["correct"] is False and r["event_recorded"]
    meta = _last_event_meta(tmp_path / "inject.db", "answer")
    assert meta["q"] == stmt            # 题面原文截断（未超 240 → 全文）
    assert meta["ans"] == "0"           # 学生答案截断


def test_answer_event_meta_q_math_safe_truncation(monkeypatch, tmp_path):
    """meta.q 用数学安全截断（对齐 _safe_trunc_math）：裸 [:240] 会切断 LaTeX 命令，
    q 落库值必须在数学区/命令边界处安全收尾。"""
    import v2api as _v2api
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    cli = _make_client(monkeypatch, tmp_path)
    # 238 字符后跟公式：裸 [:240] = 238 字符 + "$\"（正切断在 \\frac 命令中间）
    long_stmt = "a" * 238 + "$\\frac{1}{2}$"
    assert long_stmt[:240].endswith("\\")               # 裸截断确实不安全（前提成立）
    assert _v2api._safe_trunc_math(long_stmt, 240) != long_stmt[:240]
    r = cli.post("/v2/answer", json={
        "pack_id": "calculus", "kp": KP, "qtype": "fill",
        "student_answer": "0", "standard_answer": "1",
        "statement_md": long_stmt, "difficulty": "基础",
        "attribution_override": "计算失误",
    }).json()
    assert r["ok"]
    meta = _last_event_meta(tmp_path / "inject.db", "answer")
    assert meta["q"] == _v2api._safe_trunc_math(long_stmt, 240)
    assert not meta["q"].endswith("\\")


def test_selfassess_event_meta_records_q_ans(monkeypatch, tmp_path):
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    cli = _make_client(monkeypatch, tmp_path)
    r = cli.post("/v2/selfassess", json={
        "pack_id": "calculus", "kp": KP, "qtype": "solution",
        "student_steps": ["第一步", "第二步"], "grade": "不会",
        "statement_md": "证明题题面",
    }).json()
    assert r["ok"]
    meta = _last_event_meta(tmp_path / "inject.db", "selfassess")
    assert meta["q"] == "证明题题面"
    assert meta["ans"] == "第一步\n第二步"


# --------------------------------------------------------------------------- #
# F3：/v2/turn 集成（假 LLM 捕获 prompt，绝不触网）
# --------------------------------------------------------------------------- #
def test_turn_prompt_contains_memory_after_wrong_answer(monkeypatch, tmp_path):
    """答错 → 再次出题：prompt 含「错因记忆」块且含真实错因类型与题面摘录。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "0")  # LLM 路径需离开 demo 门（chat_json 已打桩）
    cli = _make_client(monkeypatch, tmp_path)
    stmt = "求 $\\lim_{x\\to0}\\frac{\\sin x}{x}$。"
    ans = cli.post("/v2/answer", json={
        "pack_id": "calculus", "kp": KP, "qtype": "fill",
        "student_answer": "0", "standard_answer": "1",
        "statement_md": stmt, "difficulty": "基础",
        "attribution_override": "计算失误",
    }).json()
    assert ans["ok"] and ans["correct"] is False

    captured = []
    monkeypatch.setattr(generate, "chat_json", _fake_chat_json(captured))
    t = cli.post("/v2/turn", json={"kp_id": KP, "pack_id": "calculus",
                                   "qtype": "fill"}).json()
    assert t["ok"], t
    assert captured, "应捕获到绿标出题 prompt"
    assert any("学生个人错因记忆" in p and "计算失误" in p for p in captured)
    assert any("题干摘录：求" in p for p in captured)   # F1 落的 q 被召回进 prompt


def test_turn_prompt_fresh_student_has_no_memory_block(monkeypatch, tmp_path):
    """空库/新学生：prompt 不含记忆块（零注入零影响）。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    cli = _make_client(monkeypatch, tmp_path)
    captured = []
    monkeypatch.setattr(generate, "chat_json", _fake_chat_json(captured))
    t = cli.post("/v2/turn", json={"kp_id": KP, "pack_id": "calculus",
                                   "qtype": "fill"}).json()
    assert t["ok"], t
    assert captured
    assert all("学生个人错因记忆" not in p for p in captured)


def test_variant_llm_flow_injects_memory(monkeypatch, tmp_path):
    """变式流（非家族 kp 的 LLM 变式路径）：记忆块经 generate_variant 注入 prompt；
    源题事件被剔除出记忆摘录——题面只经 variant_of 进【变式要求】一次，不双重注入；
    其他错题的摘录与归因统计（含源题的）仍在。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    cli = _make_client(monkeypatch, tmp_path)
    # 两道错题：先旧后源（_source_event 取最近一条错题当源题）
    a1 = cli.post("/v2/answer", json={
        "pack_id": "calculus", "kp": KP, "qtype": "fill",
        "student_answer": "0", "standard_answer": "1",
        "statement_md": "旧错题另一道题面", "difficulty": "基础",
        "attribution_override": "计算失误",
    }).json()
    a2 = cli.post("/v2/answer", json={
        "pack_id": "calculus", "kp": KP, "qtype": "fill",
        "student_answer": "0", "standard_answer": "1",
        "statement_md": "母题题面XYZ", "difficulty": "基础",
        "attribution_override": "概念混淆",
    }).json()
    assert a1["ok"] and a2["ok"] and a1["correct"] is False and a2["correct"] is False

    captured = []
    monkeypatch.setattr(generate, "chat_json", _fake_chat_json(captured))
    v = cli.post("/v2/variant", json={"kp_id": KP, "pack_id": "calculus",
                                      "qtype": "fill"}).json()
    assert v["ok"], v
    assert captured
    # 记忆块在（含归因统计，且源题的归因也计入）
    assert any("学生个人错因记忆" in p and "概念混淆" in p for p in captured)
    assert any("错 2 次" in p for p in captured)
    # 其他错题摘录保留；源题题面不再以「题干摘录」形式重复出现（只走【变式要求】）
    assert any("题干摘录：旧错题另一道题面" in p for p in captured)
    assert all("题干摘录：母题题面XYZ" not in p for p in captured)


# --------------------------------------------------------------------------- #
# demo 模式：空 profiles/空库零注入，零 API 链路不破
# --------------------------------------------------------------------------- #
def test_demo_mode_zero_api_unbroken_with_history(monkeypatch, tmp_path):
    """demo（MATHFORGE_DEMO=1）下有错题历史：家族 kp 出题仍零 API 可用
    （确定性族不消费 prompt，记忆注入不破坏演示链路）；非家族 kp 仍走既有降级引导。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "1")  # conftest 已密封为 1，显式重申
    cli = _make_client(monkeypatch, tmp_path)
    ans = cli.post("/v2/answer", json={
        "pack_id": "calculus", "kp": FAMILY_KP, "qtype": "fill",
        "student_answer": "0", "standard_answer": "1",
        "statement_md": "罗尔题面", "difficulty": "基础",
        "attribution_override": "概念混淆",
    }).json()
    assert ans["ok"] and ans["correct"] is False

    t = cli.post("/v2/turn", json={"kp_id": FAMILY_KP, "pack_id": "calculus",
                                   "qtype": "fill"}).json()
    assert t["ok"], t                                   # 包内族命中，零 API
    assert (t["question"]["statement_md"] or "").strip()

    t2 = cli.post("/v2/turn", json={"kp_id": KP, "pack_id": "calculus",
                                    "qtype": "fill"}).json()
    assert t2["ok"] is False and t2.get("code") == "demo_llm_unavailable"


def test_demo_empty_db_family_turn_unchanged(monkeypatch, tmp_path):
    """demo + 空库：家族 kp 直接出题（新库无事件 → 无注入 → 与预热缓存一致）。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    cli = _make_client(monkeypatch, tmp_path)
    t = cli.post("/v2/turn", json={"kp_id": FAMILY_KP, "pack_id": "calculus",
                                   "qtype": "fill"}).json()
    assert t["ok"], t
    assert (t["question"]["statement_md"] or "").strip()
