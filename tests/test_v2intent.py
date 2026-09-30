# B1 两页 UI 后端：意图解析（20 例验收集 ≥18）、变式溯源、空态推荐、answer meta 增强
import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("MATHFORGE_DEMO", "1")
import v2intent
import mem2
import pack_loader


@pytest.fixture()
def db_env(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("MATHFORGE_V2_DB", path)
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    c = mem2.connect(path)
    mem2.init_schema(c)
    yield path
    c.close()
    if os.path.exists(path):
        os.unlink(path)


def _client():
    from fastapi.testclient import TestClient
    import app as appmod
    return TestClient(appmod.app)


# ---- 20 例验收集：16 句图内 kp 名 + 2 句别名 + 2 句无 kp（期望 miss） ----
_GRAPH_KPS = [
    "拉格朗日中值定理", "罗尔定理", "分部积分", "二重积分", "行列式计算",
    "矩阵运算与逆矩阵", "特征值与特征向量", "渐近线", "洛必达法则",
    "参数方程求导", "全概率与贝叶斯公式", "期望与方差", "复合函数求导（链式法则）",
    "定积分计算", "函数连续与间断", "矩阵的秩",
]
_ALIAS_SENTS = ["练一下贝叶斯", "行列式来一道"]
_MISS_SENTS = ["随便出道题", "今天状态不好，换一个吧"]


def _kp_ids() -> dict:
    out = {}
    for pack in pack_loader.list_packs():
        p = pack_loader.load_pack(pack)
        for k in p["kp_graph"]:
            out[k["name"]] = k["id"]
    return out


def test_intent_20_case_hit_rate():
    """固定 20 例：16 图内名 + 2 别名应命中（≥18/20）；2 无 kp 必须 miss 且回退。"""
    kp_ids = _kp_ids()
    hit, miss_ok = 0, 0
    for name in _GRAPH_KPS:
        res = v2intent.parse(f"{name} 来一道题")
        assert res["ok"] is True, f"{name} 应命中: {res}"
        slots = res["slots"]
        assert slots["kp_id"] == kp_ids.get(name, slots["kp_id"]), f"{name} 槽位错"
        hit += 1
    for s in _ALIAS_SENTS:
        res = v2intent.parse(s)
        assert res["ok"] is True and res["confidence"] in ("high", "mid"), s
        hit += 1
    for s in _MISS_SENTS:
        res = v2intent.parse(s)
        assert res["ok"] is False, s
        miss_ok += 1
    assert hit >= 18 and miss_ok == 2, f"hit={hit} miss={miss_ok}（验收：命中≥18 且低置信不产题）"


def test_intent_difficulty_slot():
    res = v2intent.parse("来一道进阶的拉格朗日")
    assert res["ok"] and res["slots"]["difficulty"] == "进阶"
    res2 = v2intent.parse("随便出道题")
    assert res2["ok"] is False and res2["confidence"] == "low"


def test_api_intent_hit_writes_event(db_env):
    cli = _client()
    r = cli.post("/v2/intent", json={"text": "练一下拉格朗日"}).json()
    assert r["ok"] and r["slots"]["kp_id"] == "calc.lagrange"
    c = mem2.connect(db_env)
    n = c.execute("SELECT COUNT(*) FROM attempt_events WHERE mode='intent'").fetchone()[0]
    c.close()
    assert n >= 1  # 命中落事件，不绕记忆闭环


def test_api_intent_miss_fallback(db_env):
    cli = _client()
    r = cli.post("/v2/intent", json={"text": "随便出道题"}).json()
    assert r["ok"] is False and r["fallback"]  # 回退推荐，不生成


def test_variant_history_with_source(db_env):
    c = mem2.connect(db_env)
    mem2.append_event(c, pack="calculus", kp="calc.rolle", qtype="fill", mode="answer",
                      result="wrong", attribution={"type": "概念混淆", "conf": 0.9},
                      meta={"stmt_summary": "设 $f(x)=x^2-1$，求罗尔 $\\xi$。",
                            "standard_summary": "1", "difficulty": "基础"},
                      ts=1780000100.0, day="2026-09-03")
    c.close()
    cli = _client()
    r = cli.post("/v2/variant", json={"kp_id": "calc.rolle", "qtype": "fill"}).json()
    assert r["ok"] is True
    prov = r["provenance"]
    assert prov["source_kind"] == "history" and prov["source_summary"]
    assert prov["changes"] and prov["consistency"] is True  # 变式必须带溯源区块
    assert r["question"]["statement_md"]


def test_variant_family_no_history(db_env):
    cli = _client()
    r = cli.post("/v2/variant", json={"kp_id": "calc.integral.byparts", "qtype": "fill"}).json()
    assert r["ok"] and r["provenance"]["source_kind"] == "family"
    assert any(ch["type"] == "family_instance" for ch in r["provenance"]["changes"])


def test_variant_need_mother(db_env):
    """无源题且无确定性族 → 提示先做母题，不裸生成。"""
    cli = _client()
    r = cli.post("/v2/variant", json={"kp_id": "calc.continuity", "qtype": "fill"}).json()
    assert r["ok"] is False and r["code"] == "need_mother"
    assert "question" not in r


def test_recommend_start_basic_zero_api(db_env):
    cli = _client()
    items = cli.get("/v2/recommend-start").json()["items"]
    assert len(items) == 3
    zero = v2intent.zero_api_kp_ids()
    for it in items:
        assert it["kp"] in zero


def test_answer_meta_enhancement(db_env):
    """/v2/answer 落库应带题干/答案摘要 meta（供 /v2/variant 源题复用）。"""
    cli = _client()
    # 直接调 answer：包内族出题 → 用其题目作答（student=标准答案保证 correct 路径）
    q = cli.post("/v2/turn", json={"kp_id": "calc.rolle", "qtype": "fill"}).json()["question"]
    ans = q["answer_sympy"] if q.get("answer_sympy") else "0"
    r = cli.post("/v2/answer", json={
        "pack_id": "calculus", "kp": "calc.rolle", "qtype": "fill",
        "student_answer": ans, "standard_answer": q.get("answer_sympy") or ans,
        "statement_md": q["statement_md"], "analysis": q.get("analysis") or "",
        "difficulty": "基础",
    }).json()
    assert r["ok"] is True
    c = mem2.connect(db_env)
    row = c.execute(
        "SELECT meta FROM attempt_events WHERE mode='answer' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    c.close()
    meta = json.loads(row[0])
    assert meta.get("stmt_summary") and meta.get("standard_summary")


# ---- 模块 E：意图歧义确认卡（问而不猜，2026-09-28） ----
def test_intent_alias_ambiguous_mvz():
    """「中值定理」一词多义（lagrange+rolle）→ ambiguous=true，候选含两者且含首选。"""
    r = v2intent.parse("中值定理 来一道题")
    assert r["ok"] is True and r["ambiguous"] is True
    ids = [a["kp_id"] for a in r["alternatives"]]
    assert "calc.lagrange" in ids and "calc.rolle" in ids
    assert len(ids) == len(set(ids)) and len(ids) <= 3
    assert r["alternatives"][0]["kp_id"] == r["slots"]["kp_id"]   # 首选在候选首位
    for a in r["alternatives"]:
        assert set(a) == {"kp_id", "kp_name", "pack_id"} and a["kp_name"]
    # 歧义不改变既有语义：ok 仍 True、confidence 仍 high、slots 仍是旧版首选
    assert r["confidence"] == "high" and r["slots"]["kp_id"] == "calc.lagrange"


def test_intent_single_alias_not_ambiguous():
    """单目标别名/空句 → 不歧义、无候选清单（响应零歧义，老用户零感知）。"""
    for s in ("洛必达 来一道", "练一下贝叶斯"):
        r = v2intent.parse(s)
        assert r["ok"] is True and r["ambiguous"] is False and r["alternatives"] == [], s
    r2 = v2intent.parse("")
    assert r2["ok"] is False and r2["ambiguous"] is False and r2["alternatives"] == []


def test_intent_single_target_alias_resolves_multi():
    """「拉格朗日中值定理」：单目标别名明确点名，消解一词多义 → 不弹卡。"""
    r = v2intent.parse("拉格朗日中值定理 来一道题")
    assert r["ok"] is True and r["slots"]["kp_id"] == "calc.lagrange"
    assert r["ambiguous"] is False and r["alternatives"] == []


# ---- 模块 E 复审修复（2026-09-29）：blocker 消解先行 / major demo 可达性过滤 ----
def test_intent_alias_single_hit_resolves_slots_same_source():
    """复审 blocker 回归：混名（单目标别名 + 多义别名同句）→ 消解先行，slots 首选
    从单目标命中集出——学生点名罗尔绝不被静默出成拉格朗日（旧缺陷四连）。"""
    for s in ("罗尔中值定理", "罗尔 中值定理", "中值定理 罗尔", "练罗尔的中值定理"):
        r = v2intent.parse(s)
        assert r["ok"] is True, s
        assert r["slots"]["kp_id"] == "calc.rolle", f"{s} → {r['slots']['kp_id']}"
        assert r["ambiguous"] is False and r["alternatives"] == [], s
    # 对照组：单目标「拉格朗日」点名 → lagrange（原已正确，防回归）
    r2 = v2intent.parse("拉格朗日中值定理 来一道题")
    assert r2["slots"]["kp_id"] == "calc.lagrange"
    assert r2["ambiguous"] is False and r2["alternatives"] == []


def test_intent_alias_mixed_multi_single_ambiguous():
    """混名歧义：两个单目标别名同句 → 歧义由单目标命中集决定（长词「拉格朗日」
    优先），多义别名只作补充候选、不改变首选；不变式 slots 首选 == alternatives[0]。"""
    r = v2intent.parse("罗尔 拉格朗日 中值定理 来一道")
    assert r["ok"] is True and r["ambiguous"] is True
    ids = [a["kp_id"] for a in r["alternatives"]]
    assert ids[0] == r["slots"]["kp_id"] == "calc.lagrange"
    assert "calc.rolle" in ids and len(ids) <= 3
    # 单目标唯一时多义别名被消解：不弹卡，按点名走（多目标命中被丢弃）
    r2 = v2intent.parse("贝叶斯 中值定理 来一道")
    assert r2["slots"]["kp_id"] == "prob.total_bayes"
    assert r2["ambiguous"] is False and r2["alternatives"] == []


def test_intent_demo_filters_unreachable_alternatives(monkeypatch):
    """复审 major：demo 态确认卡候选只保留 zero_api 可出题的 kp（不可达 la.eigen
    被滤掉）；旧首选不可达时 slots 对齐过滤后 alternatives[0]（不变式不破）。"""
    monkeypatch.setattr(v2intent, "_ALIASES", {
        "可达歧义词": ["la.eigen", "calc.lagrange", "calc.rolle"]})
    zero = v2intent.zero_api_kp_ids()
    assert "la.eigen" not in zero and {"calc.lagrange", "calc.rolle"} <= zero  # 前提
    r = v2intent.parse("可达歧义词 来一道")
    assert r["ok"] is True and r["ambiguous"] is True
    ids = [a["kp_id"] for a in r["alternatives"]]
    assert ids == ["calc.lagrange", "calc.rolle"]      # 不可达项被过滤
    assert all(i in zero for i in ids)
    assert r["slots"]["kp_id"] == ids[0]               # 旧首选 eigen 不可达 → 重定向


def test_intent_demo_filter_left_single_no_card(monkeypatch):
    """复审 major：demo 过滤后仅剩 1 个可达候选 → 不弹卡（ambiguous=false），
    slots 直达该候选（不用再问，直接按单候选走）。"""
    monkeypatch.setattr(v2intent, "_ALIASES", {
        "单可达歧义词": ["la.eigen", "calc.rolle"]})
    r = v2intent.parse("单可达歧义词 来一道")
    assert r["ok"] is True and r["ambiguous"] is False and r["alternatives"] == []
    assert r["slots"]["kp_id"] == "calc.rolle"


def test_intent_demo_graph_ambiguous_filtered_to_single():
    """demo 态图内双高分含不可达 kp（mvt.inequality）→ 过滤后单候选不弹卡，
    second 随卡消失（不指向出不了题的 kp）。"""
    r = v2intent.parse("不等式证明 来一道题")
    assert r["ok"] is True and r["ambiguous"] is False and r["alternatives"] == []
    assert r["slots"]["kp_id"] == "calc.lagrange"      # 唯一可达候选
    assert "second" not in r


def test_intent_online_no_reachability_filter(monkeypatch):
    """在线态（假 key）不过滤：demo 下不可达的 kp 仍进确认卡候选（LLM 兜底可出题）。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(v2intent, "_ALIASES", {
        "在线歧义词": ["calc.lagrange", "calc.rolle", "la.eigen"]})
    r = v2intent.parse("在线歧义词 来一道")
    assert r["ok"] is True and r["ambiguous"] is True
    assert [a["kp_id"] for a in r["alternatives"]] == \
        ["calc.lagrange", "calc.rolle", "la.eigen"]
    assert r["slots"]["kp_id"] == r["alternatives"][0]["kp_id"]


def test_intent_alias_truncate_word_length_order(monkeypatch):
    """复审 minor：不同词长的别名同句命中 → 按（同优先级下）词长降序排序后截前 3，
    长词优先；断言不依赖 _ALIASES 插入序（插入序第一个词最短、必被截掉）。"""
    monkeypatch.setattr(v2intent, "_ALIASES", {
        "短": "la.det.calc",            # 插入序第 1、词长最短 → 排序垫底被截掉
        "中等词": "prob.classical",     # 插入序第 2、词长次短 → 第 3 位
        "更长长词": "calc.rolle",       # 插入序第 3、词长更长 → 第 2 位
        "最最最长词": "calc.lagrange",  # 插入序第 4、词长最长 → 首选
    })
    r = v2intent.parse("短 中等词 更长长词 最最最长词 来一道")
    assert r["ok"] is True and r["ambiguous"] is True
    assert r["slots"]["kp_id"] == r["alternatives"][0]["kp_id"] == "calc.lagrange"
    assert [a["kp_id"] for a in r["alternatives"]] == \
        ["calc.lagrange", "calc.rolle", "prob.classical"]


def test_intent_graph_double_match_ambiguous(monkeypatch):
    """图内双高分（两个 kp 的 typical_forms 同句命中）→ ambiguous=true；second 兼容保留。
    （在线态验证纯歧义语义：mvt.inequality 在 demo 下不可达、会被确认卡候选过滤，
    demo 行为另见 test_intent_demo_graph_ambiguous_filtered_to_single。）"""
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    r = v2intent.parse("不等式证明 来一道题")
    assert r["ok"] is True and r["ambiguous"] is True
    assert [a["kp_id"] for a in r["alternatives"]] == \
        ["calc.lagrange", "calc.mvt.inequality"]
    assert r["second"] == r["alternatives"][1]     # 旧 second 字段并入候选清单且保留
    assert r["confidence"] == "mid"                # confidence 语义不变


def test_intent_alternatives_capped_at_3(monkeypatch):
    """候选 >3 → 按现有优先级排序后截前 3（含首选）。（在线态验证纯截断语义：
    calc.limit.basic 在 demo 下不可达、会被确认卡候选过滤，demo 行为另见
    test_intent_demo_* 用例。）"""
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(v2intent, "_ALIASES", {
        "歧义测试词": ["calc.lagrange", "calc.rolle", "calc.limit.basic", "la.eigen"]})
    r = v2intent.parse("歧义测试词 来一道")
    assert r["ok"] is True and r["ambiguous"] is True
    assert [a["kp_id"] for a in r["alternatives"]] == \
        ["calc.lagrange", "calc.rolle", "calc.limit.basic"]
    assert r["slots"]["kp_id"] == "calc.lagrange"


def test_api_intent_ambiguous_passthrough(db_env):
    """意图端点透传 ambiguous/alternatives；单候选响应照旧（仅多出新字段）。"""
    cli = _client()
    r = cli.post("/v2/intent", json={"text": "中值定理 来一道题"}).json()
    assert r["ok"] is True and r["ambiguous"] is True
    ids = [a["kp_id"] for a in r["alternatives"]]
    assert "calc.lagrange" in ids and "calc.rolle" in ids
    assert "slots" in r and "second" in r          # 既有字段全保留
    r2 = cli.post("/v2/intent", json={"text": "练一下拉格朗日"}).json()
    assert r2["ok"] is True and r2["ambiguous"] is False and r2["alternatives"] == []


def test_db_clean(db_env):
    """测试全部跑在临时库；真实 mathforge.db 的行数快照必须不变。

    2026-09-12 起真实库允许合法非测试数据（复评走查/用户自用），断言从「0 行」
    放宽为「本测试会话内不新增」——基线在模块导入时捕获（见 tests/test_v2api.py 同名守卫）。"""
    import sqlite3
    real = sqlite3.connect(os.path.join(ROOT, "mathforge.db"))
    n = real.execute("SELECT COUNT(*) FROM attempt_events").fetchone()[0]
    real.close()
    import test_v2api as _guard
    assert n == _guard.BASELINE_N, f"真实库 attempt_events 行数漂移：{_guard.BASELINE_N} → {n}"

