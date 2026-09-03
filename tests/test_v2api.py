# v2 填空闭环 HTTP 接线层测试（v2api.py）
#
# 用 FastAPI TestClient（httpx 已具备）直连 app，覆盖：
#   包清单/包明细 → /v2/turn 出题 → /v2/answer 判分·归因·事件·决策 → /v2/review、/v2/stats
# 隔离纪律：
#   - MATHFORGE_DEMO=1 强制零 API（缓存未命中即降级，绝不出网）
#   - MATHFORGE_V2_DB 指向临时库，真实 mathforge.db 全程不写；
#     收尾 test_real_db_untouched 断言真实库 attempt_events 仍为 0 行（多余行只清本轮新增 id）

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


def test_answer_correct_closes_loop(client):
    t = _turn(client)
    q = t["question"]
    r = client.post("/v2/answer", json={
        "pack_id": t["pack_id"], "kp": t["kp"]["id"], "qtype": "fill",
        "student_answer": q["answer_sympy"], "standard_answer": q["answer_sympy"],
        "statement_md": q["statement_md"], "analysis": q.get("analysis"),
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
    """答错未给 override → 走 attribute_error（演示模式返回兜底四类标签，不阻塞闭环）。"""
    t = _turn(client)
    q = t["question"]
    d = client.post("/v2/answer", json={
        "pack_id": t["pack_id"], "kp": t["kp"]["id"], "qtype": "fill",
        "student_answer": "999999", "standard_answer": q["answer_sympy"],
        "statement_md": q["statement_md"], "difficulty": "基础",
    }).json()
    assert d["correct"] is False
    assert d["attribution"] in ATTR_CLASSES
    assert d["overridden"] is False
    assert d["decision"]["rule"] in VALID_RULES


def test_answer_attribution_override_wins(client):
    t = _turn(client)
    q = t["question"]
    d = client.post("/v2/answer", json={
        "pack_id": t["pack_id"], "kp": t["kp"]["id"], "qtype": "fill",
        "student_answer": "-12345", "standard_answer": q["answer_sympy"],
        "statement_md": q["statement_md"], "attribution_override": "计算失误",
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
    assert json.loads(rows[1]["attribution"])["type"] in ATTR_CLASSES


def test_review_and_stats_with_data(client):
    rv = client.get("/v2/review?limit=20")
    assert rv.status_code == 200
    items = rv.json()["items"]
    assert len(items) >= 1
    kp_item = next(i for i in items if i["kp"] == "calc.rolle")
    assert kp_item["n"] == 3
    assert kp_item["last_attribution"]["type"] in ATTR_CLASSES
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
    assert r1["mastery_after"]["n"] == 1 and r1["mastery_after"]["value"] == 1.0
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
        "statement_md": q["statement_md"], "analysis": q.get("analysis"),
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
        "statement_md": q["statement_md"], "analysis": q.get("analysis"),
        "standard_answer": q["answer_sympy"], "steps": ["x=1", "x=2"], "kp": d["kp"]["id"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body.get("degraded") is True


# --------------------------------------------------------------------------- #
# 收尾：真实库零污染
# --------------------------------------------------------------------------- #
def test_real_db_untouched(client):
    """真实 mathforge.db 的 attempt_events 必须回到 0 行（红线）。

    本轮全程写临时库，故只需断言；若发现本轮新增行（id > 基线），先清理再断言。
    """
    conn = sqlite3.connect(REAL_DB)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='attempt_events'"
        ).fetchone()
        if exists:
            leaked = conn.execute(
                "SELECT COUNT(*) FROM attempt_events WHERE id > ?", (BASELINE_MAX_ID,)
            ).fetchone()[0]
            if leaked:  # 清理逻辑：只删本轮新增，绝不动基线行
                conn.execute("DELETE FROM attempt_events WHERE id > ?", (BASELINE_MAX_ID,))
                conn.commit()
            n = conn.execute("SELECT COUNT(*) FROM attempt_events").fetchone()[0]
            assert leaked == 0, f"真实库被污染 {leaked} 行（已清理）"
        else:
            n = 0
    finally:
        conn.close()
    assert n == BASELINE_N == 0, f"真实库 attempt_events 应为 0 行，实际 {n}（基线 {BASELINE_N}）"


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
