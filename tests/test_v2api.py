# v2 填空闭环 HTTP 接线层测试（v2api.py）
#
# 用 FastAPI TestClient（httpx 已具备）直连 app，覆盖：
#   包清单/包明细 → /v2/turn 出题 → /v2/answer 判分·归因·事件·决策 → /v2/review、/v2/stats
# 隔离纪律：
#   - MATHFORGE_DEMO=1 强制零 API（缓存未命中即降级，绝不出网）
#   - MATHFORGE_V2_DB 指向临时库，真实 mathforge.db 全程不写；
#     收尾 test_real_db_untouched 断言真实库 attempt_events 行数快照不变（2026-09-12 起允许合法非测试数据）

import json
import os
import sqlite3
import sys
from datetime import date

import pytest

import mem2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["MATHFORGE_DEMO"] = "1"  # 零 API：LLM 只读缓存，未命中走既有降级链

TMP_DIR = os.path.join(ROOT, ".pytest_tmp_v2api")
TMP_DB = os.path.join(TMP_DIR, "mathforge_test_tmp.db")
REAL_DB = os.path.join(ROOT, "mathforge.db")

VALID_RULES = {"P1", "P2", "P3", "P4", "P5", "P6"}
ATTR_CLASSES = {"概念混淆", "计算失误", "方法选错", "审题错误"}
ATTR_OBSERVABLE = ATTR_CLASSES | {"未归因"}  # 演示/无 key 时 attribute_error 兜底为「未归因」(P0 2026-09-12)


def _real_db_baseline() -> tuple[int, int]:
    """真实库 attempt_events 的 (行数, 最大 id)；表不存在视为 (0, 0)。"""
    conn = sqlite3.connect(REAL_DB)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='attempt_events'"
        ).fetchone()
        if not row:
            return 0, 0
        n = conn.execute("SELECT COUNT(*) FROM attempt_events").fetchone()[0]
        mx = conn.execute("SELECT COALESCE(MAX(id), 0) FROM attempt_events").fetchone()[0]
        return n, mx
    finally:
        conn.close()


BASELINE_N, BASELINE_MAX_ID = _real_db_baseline()


@pytest.fixture(scope="module")
def client():
    os.makedirs(TMP_DIR, exist_ok=True)
    if os.path.exists(TMP_DB):
        os.remove(TMP_DB)
    os.environ["MATHFORGE_V2_DB"] = TMP_DB
    os.environ["MATHFORGE_PROFILES_DIR"] = TMP_DIR  # 档案库也隔离到临时目录（PM 复评 P0）

    from fastapi.testclient import TestClient

    import app as app_mod

    with TestClient(app_mod.app) as c:
        yield c
    os.environ.pop("MATHFORGE_V2_DB", None)


def _tmp_conn():
    conn = sqlite3.connect(TMP_DB)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------- #
# 包清单 / 包明细
# --------------------------------------------------------------------------- #
def test_packs_list_three(client):
    r = client.get("/v2/packs")
    assert r.status_code == 200
    packs = r.json()["packs"]
    ids = {p["id"] for p in packs}
    assert {"calculus", "linear", "probability"} <= ids
    assert len(packs) == 3
    assert all(p["kp_count"] > 0 and p["name"] for p in packs)


def test_pack_detail_kp_tree_with_parents(client):
    r = client.get("/v2/packs/calculus")
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == "calculus" and d["name"] == "高等数学"
    kps = {k["id"]: k for k in d["kps"]}
    assert "calc.rolle" in kps
    rolle = kps["calc.rolle"]
    assert rolle["name"] == "罗尔定理"
    assert rolle["parents"] == ["calc.continuity", "calc.derivative.basic"]
    assert all(p in kps for p in rolle["parents"])  # parents 可解析（UI 前置链可渲染）
    assert d["strategy"]["policy"]["p5_streak"] >= 1


def test_pack_detail_unknown_404(client):
    assert client.get("/v2/packs/nope").status_code == 404


# --------------------------------------------------------------------------- #
# 空库两态：review / stats 不 500
# --------------------------------------------------------------------------- #
def test_review_and_stats_empty(client):
    rv = client.get("/v2/review?limit=5")
    assert rv.status_code == 200
    assert rv.json() == {"items": [], "total": 0}

    st = client.get("/v2/stats")
    assert st.status_code == 200
    d = st.json()
    assert d["kp_count"] == 0 and d["event_count"] == 0
    assert {p["id"] for p in d["packs"]} == {"calculus", "linear", "probability"}


# --------------------------------------------------------------------------- #
# 出题
# --------------------------------------------------------------------------- #
def test_turn_rolle_family_zero_api(client):
    """kp_id=calc.rolle → 中文名「罗尔定理」命中确定性家族链（零 API 可用）。"""
    r = client.post("/v2/turn", json={"kp_id": "calc.rolle", "qtype": "fill",
                                      "difficulty": "基础"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True, d
    assert d["pack_id"] == "calculus"
    assert d["kp"]["name"] == "罗尔定理"
    assert d["kp"]["parents"] == ["calc.continuity", "calc.derivative.basic"]
    q = d["question"]
    assert q["statement_md"] and "$" in q["statement_md"]
    assert q["answer_sympy"]
    assert q["qtype"] == "fill" and q["gen_qtype"] == "calculation"
    assert q["routed"] == "family"          # 家族路由证据（构造即正确）
    assert d["route"]["path"] == "stage1"


def test_turn_explicit_pack(client):
    r = client.post("/v2/turn", json={"kp_id": "calc.rolle", "pack_id": "calculus"})
    d = r.json()
    assert r.status_code == 200 and d["ok"] is True
    assert d["route"]["path"] == "explicit"


def test_turn_unknown_kp_returns_ok_false(client):
    r = client.post("/v2/turn", json={"kp_id": "calc.does.not.exist"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False and d["reason"]


def test_turn_unknown_pack_returns_ok_false(client):
    r = client.post("/v2/turn", json={"kp_id": "calc.rolle", "pack_id": "no_such_pack"})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_turn_kp_not_in_given_pack(client):
    r = client.post("/v2/turn", json={"kp_id": "la.eigen", "pack_id": "calculus"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False and "calc" not in (d.get("kp") or {})


# --------------------------------------------------------------------------- #
# 作答闭环：turn → answer（对/错/override）→ 事件 → 决策
# --------------------------------------------------------------------------- #
def _turn(client, kp_id="calc.rolle"):
    d = client.post("/v2/turn", json={"kp_id": kp_id, "qtype": "fill",
                                      "difficulty": "基础"}).json()
    assert d["ok"] is True, d
    return d


_SEEN_FP: set = set()  # 模块级已答指纹集合（同题去重语义下拿新题用）


def _turn_fresh(client, kp_id="calc.rolle", tries=10):
    """拿一道「本模块还没答过」的新题（跨题去重语义下的测试基建）。"""
    for _ in range(tries):
        t = _turn(client, kp_id)
        fp = t["question"].get("question_fp", "")
        if fp and fp not in _SEEN_FP:
            _SEEN_FP.add(fp)
            return t
        if not fp:  # 无指纹的旧客户端形态：直接返回
            return t
    return _turn(client, kp_id)  # 兜底（全重复时概率性接受）


def test_answer_correct_closes_loop(client):
    t = _turn(client)
    q = t["question"]
    r = client.post("/v2/answer", json={
        "pack_id": t["pack_id"], "kp": t["kp"]["id"], "qtype": "fill",
        "student_answer": q["answer_sympy"], "standard_answer": q["answer_sympy"],
        "statement_md": q["statement_md"], "question_fp": q.get("question_fp", ""), "analysis": q.get("analysis"),
        "difficulty": "基础",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["correct"] is True
    assert d["attribution"] is None          # 答对不归因
    assert d["decision"]["rule"] in VALID_RULES
    assert d["decision"]["action"]["kp_target"]
    assert d["event_id"] > 0


def test_answer_wrong_llm_attribution(client):
    """答错未给 override → 走 attribute_error（演示模式兜底「未归因」，不阻塞闭环，
    且不再假称「计算失误」污染记忆——P0 2026-09-12）。"""
    t = _turn(client)
    q = t["question"]
    d = client.post("/v2/answer", json={
        "pack_id": t["pack_id"], "kp": t["kp"]["id"], "qtype": "fill",
        "student_answer": "999999", "standard_answer": q["answer_sympy"],
        "statement_md": q["statement_md"], "question_fp": q.get("question_fp", ""), "difficulty": "基础",
    }).json()
    assert d["correct"] is False
    assert d["attribution"] in ATTR_OBSERVABLE
    assert d["overridden"] is False
    assert d["decision"]["rule"] in VALID_RULES


def test_answer_attribution_override_wins(client):
    t = _turn(client)
    q = t["question"]
    d = client.post("/v2/answer", json={
        "pack_id": t["pack_id"], "kp": t["kp"]["id"], "qtype": "fill",
        "student_answer": "-12345", "standard_answer": q["answer_sympy"],
        "statement_md": q["statement_md"], "question_fp": q.get("question_fp", ""), "attribution_override": "计算失误",
        "difficulty": "进阶",
    }).json()
    assert d["correct"] is False
    assert d["attribution"] == "计算失误" and d["overridden"] is True
    # override 落在 user_override 列 → 投影按人工修正优先
    conn = _tmp_conn()
    try:
        row = conn.execute(
            "SELECT * FROM attempt_events WHERE id=?", (d["event_id"],)
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["user_override"])["type"] == "计算失误"
    # 最近错因=计算失误 → 下一步命中 P4（记忆驱动，非随机）
    assert d["decision"]["rule"] in VALID_RULES


def test_events_persisted_in_attempt_events(client):
    """事件确实落 attempt_events 表（三次作答：1 对 2 错）。"""
    conn = _tmp_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM attempt_events WHERE mode='answer' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 3
    assert [r["result"] for r in rows] == ["correct", "wrong", "wrong"]
    assert all(r["kp"] == "calc.rolle" and r["pack"] == "calculus" for r in rows)
    assert all(r["qtype"] == "fill" and r["sim"] == 0 for r in rows)
    assert json.loads(rows[1]["attribution"])["type"] in ATTR_OBSERVABLE


def test_review_and_stats_with_data(client):
    rv = client.get("/v2/review?limit=20")
    assert rv.status_code == 200
    items = rv.json()["items"]
    assert len(items) >= 1
    kp_item = next(i for i in items if i["kp"] == "calc.rolle")
    assert kp_item["n"] == 3
    assert kp_item["last_attribution"]["type"] in ATTR_OBSERVABLE
    vals = [i["decayed_value"] or 0.0 for i in items]
    assert vals == sorted(vals)          # 掌握度最低优先

    st = client.get("/v2/stats").json()
    assert st["kp_count"] >= 1
    assert st["event_count"] >= 3        # 含 policy2 决策日志事件
    assert "calc.rolle" in st["kps"]


# --------------------------------------------------------------------------- #
# 足迹热力图端点：空库=[] / 写 2 事件后含当日行
# --------------------------------------------------------------------------- #
def test_heatmap_empty_returns_empty():
    """独立临时库（空）→ /v2/heatmap 必须返回 []（不 500）。"""
    fresh = os.path.join(TMP_DIR, "heatmap_empty.db")
    if os.path.exists(fresh):
        os.remove(fresh)
    prev = os.environ.get("MATHFORGE_V2_DB")
    os.environ["MATHFORGE_V2_DB"] = fresh
    try:
        from fastapi.testclient import TestClient

        import app as app_mod

        with TestClient(app_mod.app) as c:
            r = c.get("/v2/heatmap?days=7")
            assert r.status_code == 200
            assert r.json() == []
    finally:
        if prev is None:
            os.environ.pop("MATHFORGE_V2_DB", None)
        else:
            os.environ["MATHFORGE_V2_DB"] = prev


def test_heatmap_contains_today_after_two_events(client):
    """写 2 条作答事件（1 对 1 错）→ 近 7 天含当日行且 count>=2、correct>=1。"""
    conn = _tmp_conn()
    try:
        mem2.append_event(conn, pack="calculus", kp="calc.rolle", qtype="fill",
                          mode="answer", result="correct")
        mem2.append_event(conn, pack="calculus", kp="calc.rolle", qtype="fill",
                          mode="answer", result="wrong")
    finally:
        conn.close()
    today = date.today().strftime("%Y-%m-%d")
    r = client.get("/v2/heatmap?days=7")
    assert r.status_code == 200
    rows = {x["day"]: x for x in r.json()}
    assert today in rows
    assert rows[today]["count"] >= 2
    assert rows[today]["correct"] >= 1
    assert all(set(x.keys()) == {"day", "count", "correct"} for x in rows.values())


# --------------------------------------------------------------------------- #
# 解答题模式（v2 大题 / D022 红线）：turn(solution) → selfassess → steps-attribute
# --------------------------------------------------------------------------- #
def _turn_solution(client, kp_id="calc.lagrange"):
    d = client.post("/v2/turn", json={"kp_id": kp_id, "qtype": "solution",
                                      "difficulty": "基础"}).json()
    assert d["ok"] is True, d
    return d


def test_turn_solution_rolle_and_lagrange_family_zero_api(client):
    """qtype=solution 走家族绿标链（罗尔/拉格朗日），DEMO 零 API 出题 ok。"""
    for kp_id in ("calc.rolle", "calc.lagrange"):
        d = _turn_solution(client, kp_id)
        q = d["question"]
        assert q["qtype"] == "solution" and q["gen_qtype"] == "solution"
        assert q["statement_md"] and q["answer_sympy"]
        assert q["routed"] == "family"
        assert d["kp"]["qtypes"] == ["fill", "solution"]


def test_turn_qtype_guard_rejects_unsupported(client):
    """kp.qtypes 不含所请题型（如 concept）→ 200 {ok:false, reason}，绝不 500。"""
    r = client.post("/v2/turn", json={"kp_id": "calc.rolle", "qtype": "concept"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False and d["reason"]


def test_selfassess_three_grades_map_and_event(client):
    """三档自评映射 result：会→correct / 部分会→partial / 不会→wrong；事件落表 mode=selfassess；mastery_after 返回。"""
    d = _turn_solution(client)
    q = d["question"]
    base = dict(pack_id=d["pack_id"], kp=d["kp"]["id"], qtype="solution",
                statement_md=q["statement_md"], analysis=q.get("analysis"),
                standard_answer=q["answer_sympy"])
    steps = ["设辅助函数 F(x)=...", "验证 F(a)=F(b)", "存在 ξ 使 F'(ξ)=0"]
    # 会 → correct
    r1 = client.post("/v2/selfassess", json={**base, "student_steps": steps,
                                             "grade": "会"}).json()
    assert r1["ok"] and r1["result"] == "correct"
    assert r1["mastery_after"]["n"] == 1
    assert r1["mastery_after"]["value"] == 0.7  # 首答封顶 0.7（学生复评：防虚假安全感）
    # 部分会 → partial
    r2 = client.post("/v2/selfassess", json={**base, "student_steps": steps,
                                             "grade": "部分会"}).json()
    assert r2["ok"] and r2["result"] == "partial" and r2["mastery_after"]["n"] == 2
    # 不会 → wrong
    r3 = client.post("/v2/selfassess", json={**base, "student_steps": steps,
                                             "grade": "不会"}).json()
    assert r3["ok"] and r3["result"] == "wrong" and r3["mastery_after"]["n"] == 3

    # 事件落 attempt_events，mode=selfassess，result 顺序与步数
    conn = _tmp_conn()
    try:
        rows = conn.execute(
            "SELECT result, meta FROM attempt_events WHERE mode='selfassess' "
            "AND kp=? ORDER BY id", (d["kp"]["id"],)
        ).fetchall()
    finally:
        conn.close()
    assert [r["result"] for r in rows] == ["correct", "partial", "wrong"]
    for r in rows:
        assert json.loads(r["meta"])["step_count"] == 3


def test_selfassess_attribution_carried(client):
    """selfassess 带 attribution → 事件 attribution 列记录 {type: 概念混淆}。"""
    d = _turn_solution(client)
    q = d["question"]
    r = client.post("/v2/selfassess", json={
        "pack_id": d["pack_id"], "kp": d["kp"]["id"], "qtype": "solution",
        "student_steps": ["步骤一", "步骤二"], "grade": "不会",
        "attribution": "概念混淆",
        "statement_md": q["statement_md"], "question_fp": q.get("question_fp", ""), "analysis": q.get("analysis"),
        "standard_answer": q["answer_sympy"],
    }).json()
    assert r["ok"] and r["attribution"] == "概念混淆"
    conn = _tmp_conn()
    try:
        row = conn.execute(
            "SELECT attribution FROM attempt_events WHERE mode='selfassess' "
            "AND kp=? ORDER BY id DESC LIMIT 1", (d["kp"]["id"],)
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["attribution"])["type"] == "概念混淆"


def test_selfassess_invalid_grade_ok_false(client):
    """非法 grade → 200 {ok:false}（与 /v2/turn 错误风格一致）。"""
    d = _turn_solution(client)
    r = client.post("/v2/selfassess", json={
        "pack_id": d["pack_id"], "kp": d["kp"]["id"], "qtype": "solution",
        "student_steps": ["x"], "grade": "似懂非懂",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_steps_attribute_degraded_in_demo(client):
    """MATHFORGE_DEMO=1 → /v2/steps-attribute 降级 200 {ok:false, degraded:true}，绝不 500。"""
    d = _turn_solution(client)
    q = d["question"]
    r = client.post("/v2/steps-attribute", json={
        "statement_md": q["statement_md"], "question_fp": q.get("question_fp", ""), "analysis": q.get("analysis"),
        "standard_answer": q["answer_sympy"], "steps": ["x=1", "x=2"], "kp": d["kp"]["id"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body.get("degraded") is True


# --------------------------------------------------------------------------- #
# 收尾：真实库零污染
# --------------------------------------------------------------------------- #
def test_real_db_untouched(client):
    """真实 mathforge.db 的 attempt_events 行数在测试前后不变（红线：测试绝不写真实库）。

    2026-09-12 起真实库允许含有合法的非测试数据（复评走查/用户自用产生的事件），
    故断言从「必须 0 行」放宽为「快照不变」：基线在模块导入时捕获，测试结束后必须持平。
    """
    conn = sqlite3.connect(REAL_DB)
    try:
        n = conn.execute("SELECT COUNT(*) FROM attempt_events").fetchone()[0]
    finally:
        conn.close()
    assert n == BASELINE_N, f"真实库 attempt_events 行数漂移：{BASELINE_N} → {n}（本轮测试写了真实库）"



def test_meta_sim_mode(tmp_path, monkeypatch):
    """/v2/meta：全 sim 事件 → sim_mode=True；含真实事件 → False。"""
    import v2api, mem2, sqlite3
    db = str(tmp_path / "sim.db")
    monkeypatch.setenv("MATHFORGE_V2_DB", db)
    c = mem2.connect(db); mem2.init_schema(c)
    mem2.append_event(c, pack="calculus", kp="calc.rolle", qtype="fill", mode="sim", result="wrong", sim=1, ts=1780000000.0, day="2026-09-01")
    c.close()
    from fastapi.testclient import TestClient
    import app as appmod
    cli = TestClient(appmod.app)
    m = cli.get("/v2/meta").json()
    assert m["sim_mode"] is True and m["events"] >= 1
    c = mem2.connect(db); mem2.init_schema(c)
    mem2.append_event(c, pack="calculus", kp="calc.rolle", qtype="fill", mode="answer", result="correct", sim=0, ts=1780000000.0, day="2026-09-02")
    c.close()
    m2 = cli.get("/v2/meta").json()
    assert m2["sim_mode"] is False


# --------------------------------------------------------------------------- #
# T9 demo 出题失败降级与引导
# --------------------------------------------------------------------------- #
def test_turn_demo_nonfamily_returns_guided_list(client):
    """demo 模式（MATHFORGE_DEMO=1）：calc.continuity 不在确定性家族链，
    直接 200 {ok:false, code, demo_available_kps}，不硬试 LLM、不裸抛缓存哈希。"""
    r = client.post("/v2/turn", json={"kp_id": "calc.continuity", "qtype": "fill",
                                      "difficulty": "基础"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False
    assert d["code"] == "demo_llm_unavailable"
    assert d["reason"] and "2bf09369" not in d["reason"] and "…" not in d["reason"]
    kps = d["demo_available_kps"]
    assert isinstance(kps, list) and len(kps) >= 1
    ids = {x["kp"] for x in kps}
    assert "calc.rolle" in ids          # 罗尔定理命中家族链，可零 API 出题
    assert all(set(x.keys()) >= {"kp", "name", "pack_id"} for x in kps)
    assert d["pack_id"] == "calculus"


def test_turn_demo_family_still_succeeds(client):
    """demo 模式：家族链 kp（calc.rolle / calc.lagrange）照常零 API 出题，不受降级影响。"""
    for kp_id in ("calc.rolle", "calc.lagrange"):
        r = client.post("/v2/turn", json={"kp_id": kp_id, "qtype": "fill",
                                          "difficulty": "基础"})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True, d
        assert d["question"]["routed"] == "family"
        assert "code" not in d        # 成功路径不带降级 code
        assert "demo_available_kps" not in d


def test_turn_non_demo_continuity_no_500(client, monkeypatch):
    """非 demo 下 calc.continuity（需 LLM）走生成链：无 key 时优雅降级，
    绝不 500。只断言形状（ok 键存在），不断言成功。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    r = client.post("/v2/turn", json={"kp_id": "calc.continuity", "qtype": "fill",
                                      "difficulty": "基础"})
    assert r.status_code == 200
    d = r.json()
    assert "ok" in d                   # 失败也 ok:false + code，不 500
    if not d["ok"]:
        assert d.get("code")          # 失败时带人类可读 code（非裸哈希）


# --------------------------------------------------------------------------- #
# D8-7 新确定性族接入：包内 families（分部积分 / 二重积分）零 API 出题
# --------------------------------------------------------------------------- #
def test_turn_demo_pack_families_zero_api(client):
    """demo 下 calc.integral.byparts / calc.double.int 走包内确定性族出题，
    结构与 rolle 家族路径同构：ok、有 statement/answer、verify_level=green、
    routed=family、qtype=fill、gen_qtype=calculation。"""
    for kp_id in ("calc.integral.byparts", "calc.double.int"):
        r = client.post("/v2/turn", json={"kp_id": kp_id, "qtype": "fill",
                                          "difficulty": "基础"})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True, d
        assert d["pack_id"] == "calculus"
        assert d["kp"]["id"] == kp_id
        q = d["question"]
        assert q["statement_md"] and "$" in q["statement_md"]
        assert q["answer_sympy"]
        assert q["qtype"] == "fill" and q["gen_qtype"] == "calculation"
        assert q["verify_level"] == "green"
        assert q["routed"] == "family"          # 包内族走 family 路由证据
        assert q["family"]                      # 回溯用 family 标记非空
        assert "code" not in d                 # 成功路径不带降级 code
        assert "demo_available_kps" not in d


def test_demo_available_kps_includes_pack_families(client):
    """demo 模式（MATHFORGE_DEMO=1）：以非族 kp calc.continuity 触发降级引导，
    返回的 demo_available_kps 除 v1 家族（calc.rolle）外，还应含包内确定性族
    calc.integral.byparts 与 calc.double.int。"""
    r = client.post("/v2/turn", json={"kp_id": "calc.continuity", "qtype": "fill",
                                      "difficulty": "基础"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False and d["code"] == "demo_llm_unavailable"
    kps = d["demo_available_kps"]
    ids = {x["kp"] for x in kps}
    # v1 家族
    assert "calc.rolle" in ids
    # D8-7 新接入的包内确定性族
    assert "calc.integral.byparts" in ids
    assert "calc.double.int" in ids
    assert all(set(x.keys()) >= {"kp", "name", "pack_id"} for x in kps)


def test_turn_nondemo_pack_family_byparts(client, monkeypatch):
    """非 demo 下 calc.integral.byparts 仍可出题：包内族命中不依赖 MATHFORGE_DEMO，
    走 _try_pack_family 直接出题，不经过 LLM 生成链。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    r = client.post("/v2/turn", json={"kp_id": "calc.integral.byparts", "qtype": "fill",
                                      "difficulty": "基础"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True, d            # 包内族命中，与 DEMO 无关
    assert "code" not in d
    q = d["question"]
    assert q["statement_md"] and q["answer_sympy"]
    assert q["verify_level"] == "green" and q["routed"] == "family"


# --------------------------------------------------------------------------- #
# P0 修补（2026-09-12）：填空解析预检
# --------------------------------------------------------------------------- #

def test_parse_check_endpoint(client):
    """可解析→parseable:true；乱串→parseable:false + 人类可读 error。"""
    ok = client.post("/v2/parse-check", json={"expr": "(x+1)^2 - (x^2+2x+1)"}).json()
    assert ok["ok"] is True and ok["parseable"] is True and ok["error"] is None
    bad = client.post("/v2/parse-check", json={"expr": "((( unparseable"}).json()
    assert bad["parseable"] is False and "无法识别" in bad["error"]
    bare = client.post("/v2/parse-check", json={"expr": "sin"}).json()
    assert bare["parseable"] is False  # 裸函数名：解析成功但无法判分


def test_answer_parse_error_skips_event(client):
    """不可解析作答：correct=False 但不记事件、不出决策（零学习信号不入记忆）。"""
    t = _turn(client)
    q = t["question"]
    before = client.get("/v2/stats").json()
    d = client.post("/v2/answer", json={
        "pack_id": t["pack_id"], "kp": t["kp"]["id"], "qtype": "fill",
        "student_answer": "((( ???", "standard_answer": q["answer_sympy"],
        "statement_md": q["statement_md"], "question_fp": q.get("question_fp", ""), "difficulty": "基础",
    }).json()
    assert d["ok"] is True and d["correct"] is False
    assert d.get("parse_error") and "无法识别" in d["parse_error"]
    assert d["event_recorded"] is False and d["event_id"] is None and d["decision"] is None
    after = client.get("/v2/stats").json()
    assert after.get("total", 0) == before.get("total", 0), "事件总数不应增加"


# --------------------------------------------------------------------------- #
# 拍照识别（可选 sidecar）：未启动时优雅降级
# --------------------------------------------------------------------------- #

def test_photo_recognize_sidecar_down(client, monkeypatch):
    """sidecar 不存在 → 200 + ok:false + ingest_unavailable（可选组件不阻断练习）。"""
    import base64
    png1x1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg==")
    monkeypatch.setenv("MATHFORGE_INGEST_URL", "http://127.0.0.1:1")  # 端口 1 必然连接拒绝
    r = client.post("/v2/photo-recognize",
                    files={"file": ("p.png", png1x1, "image/png")})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False
    assert d["code"] == "ingest_unavailable"
    assert "识别服务" in d["reason"]


def test_extract_answer_latex_heuristics():
    """答案候选提取启发式：推导链取最后一个等号后的值；掩码/超长放弃。"""
    from v2.v2api import _extract_answer_latex
    md = "题干……\n\n$$f'(x) = 3x^2-3$$\n\n$$\left[ \frac{x^4}{4} \right]_{1}^{3} = 12$$"
    assert _extract_answer_latex(md) == "12"
    assert _extract_answer_latex("没有数学块") is None
    assert _extract_answer_latex("$f(x) = ⟨?⟩$") is None  # unclear 掩码（sidecar 实际输出形态）放弃


# ---- 今日计划（2026-09-12）----

def test_plan_cold_start_returns_zero_api_family(client):
    """空库 → 计划全部来自零 API 确定性族 kp，且只显中文名。"""
    d = client.get("/v2/plan?n=3").json()
    assert d["ok"] is True and len(d["items"]) == 3
    for it in d["items"]:
        assert it["zero_api"] is True
        assert it["name"] and "_" not in it["name"][:2]  # 中文名，非裸 id


def test_plan_weakest_first(client):
    """有记录后：衰减掌握度最低的 kp 排最前，理由带掌握度。"""
    t = _turn_fresh(client)  # 跨题去重语义下拿一道没答过的新题
    q = t["question"]
    client.post("/v2/answer", json={
        "pack_id": t["pack_id"], "kp": t["kp"]["id"], "qtype": "fill",
        "student_answer": "999999", "standard_answer": q["answer_sympy"],
        "statement_md": q["statement_md"], "question_fp": q.get("question_fp", ""), "attribution_override": "概念混淆",
        "difficulty": "基础"})
    d = client.get("/v2/plan?n=3").json()
    assert d["items"][0]["kp_id"] == t["kp"]["id"]
    assert "掌握度" in d["items"][0]["reason"]


# ---- 每人一库（2026-09-12，AI-PM B1）：X-MF-Profile 档案隔离 ----

def test_profile_isolation(client):
    """不同档案库互不可见：stuA 作答只进 stuA 的库，默认库与 stuB 计数不变。"""
    # 档案库已隔离到 TMP_DIR（MATHFORGE_PROFILES_DIR）；本测试先清自己的档案库保证从零开始
    from v2.v2api import _profiles_dir
    for nm in ("mathforge_stuA.db", "mathforge_stuB.db"):
        f = os.path.join(_profiles_dir(), nm)
        if os.path.exists(f):
            os.remove(f)
    t = _turn(client)
    q = t["question"]
    body = {"pack_id": t["pack_id"], "kp": t["kp"]["id"], "qtype": "fill",
            "student_answer": q["answer_sympy"], "standard_answer": q["answer_sympy"],
            "statement_md": q["statement_md"], "question_fp": q.get("question_fp", ""), "difficulty": "基础"}
    before = client.get("/v2/stats").json()["event_count"]
    r = client.post("/v2/answer", json=body, headers={"X-MF-Profile": "stuA"})
    assert r.status_code == 200 and r.json()["correct"] is True
    # 默认库计数不变（事件没漏进默认库）
    assert client.get("/v2/stats").json()["event_count"] == before
    # stuB 是全新档案：自己的库从 0 开始（彼此隔离，各记各的）
    assert client.get("/v2/stats", headers={"X-MF-Profile": "stuB"}).json()["event_count"] == 0
    # stuA 能看到自己的作答（作答行 + 策略落盘行 = 2）
    assert client.get("/v2/stats", headers={"X-MF-Profile": "stuA"}).json()["event_count"] == 2
    # 档案库文件真实存在且在 profiles/ 下（清理：测试不留库文件）
    prof_dir = os.environ["MATHFORGE_PROFILES_DIR"]
    assert os.path.exists(os.path.join(prof_dir, "mathforge_stuA.db"))


def test_profile_name_sanitized(client):
    """非法档案名（路径注入）一律回落默认库，不产生任意路径文件。"""
    t = _turn(client)
    q = t["question"]
    body = {"pack_id": t["pack_id"], "kp": t["kp"]["id"], "qtype": "fill",
            "student_answer": "0", "standard_answer": q["answer_sympy"],
            "statement_md": q["statement_md"], "question_fp": q.get("question_fp", ""), "difficulty": "基础"}
    before = client.get("/v2/stats").json()["event_count"]
    r = client.post("/v2/answer", json=body,
                    headers={"X-MF-Profile": "../../evil"})
    # 非法档案名 → 422 拒绝（复评 S1：回落会破坏隔离语义），默认库不受影响
    assert r.status_code == 422
    assert r.json()["code"] == "bad_profile"
    assert client.get("/v2/stats").json()["event_count"] == before


# ---- AI 变式策略与作答通道（任务书 P2，2026-09-15） ----
#
# 策略测试 = 纯单元：内存临时库直接构造事件序列调 _pick_variant_strategy，零随机、零 HTTP。
# （第一版走 client 集成，但 /v2/turn 随机出题+跨测试共享库引入不可复现性——单元化后才稳。）

import sqlite3 as _sq


def _unit_db():
    """临时单元库（文件级 tmp 目录，用完即删）。"""
    os.makedirs(TMP_DIR, exist_ok=True)
    path = os.path.join(TMP_DIR, "unit_strategy.db")
    if os.path.exists(path):
        os.remove(path)
    conn = _sq.connect(path)
    conn.row_factory = _sq.Row
    import mem2 as _m
    _m.connect(path).close()          # 幂等建表
    return conn, path


def _ev(conn, kp, mode, result, ts):
    mem2.append_event(conn, pack="calculus", kp=kp, qtype="fill", mode=mode,
                      result=result, ts=ts)


def test_strategy_wrong_then_none():
    conn, path = _unit_db()
    try:
        _ev(conn, "kp.x", "answer", "wrong", 1000)
        from v2.v2api import _pick_variant_strategy
        assert _pick_variant_strategy(conn, "kp.x") == "contextual"
    finally:
        conn.close()
        os.remove(path)


def test_strategy_streak2_gives_multistep():
    conn, path = _unit_db()
    try:
        _ev(conn, "kp.x", "answer", "wrong", 1000)
        _ev(conn, "kp.x", "answer", "correct", 2000)
        _ev(conn, "kp.x", "answer", "correct", 3000)
        from v2.v2api import _pick_variant_strategy
        assert _pick_variant_strategy(conn, "kp.x") == "multistep"
    finally:
        conn.close()
        os.remove(path)


def test_strategy_no_events_defaults_contextual():
    conn, path = _unit_db()
    try:
        from v2.v2api import _pick_variant_strategy
        assert _pick_variant_strategy(conn, "kp.empty") == "contextual"
    finally:
        conn.close()
        os.remove(path)


def test_strategy_retry_not_counted():
    conn, path = _unit_db()
    try:
        _ev(conn, "kp.x", "answer", "wrong", 1000)
        _ev(conn, "kp.x", "retry", "correct", 2000)     # 订正不计 streak
        from v2.v2api import _pick_variant_strategy
        assert _pick_variant_strategy(conn, "kp.x") == "contextual"
    finally:
        conn.close()
        os.remove(path)


def test_answer_mode_variant_counts_in_heatmap(client):
    """mode='variant' 作答计入热力图（口径与 answer 一致，任务书 P2-b）。

    集成口径只验「计数通道」：variant 事件落库后 heatmap 当日 count 增长 ≥1。
    用 tmp 库直写事件，不依赖随机出题。
    """
    import datetime as _dt
    conn = _tmp_conn()
    try:
        today = _dt.date.today().strftime("%Y-%m-%d")
        before = conn.execute(
            "SELECT COUNT(*) FROM attempt_events WHERE day=? AND mode='variant'",
            (today,)).fetchone()[0]
        mem2.append_event(conn, pack="calculus", kp="calc.rolle", qtype="fill",
                          mode="variant", result="correct", meta={"parse_ok": True})
    finally:
        conn.close()
    hm = client.get("/v2/heatmap?days=7").json()
    today = _dt.date.today().strftime("%Y-%m-%d")
    row = [x for x in hm if x["day"] == today]
    assert row and row[0]["count"] >= before + 1
