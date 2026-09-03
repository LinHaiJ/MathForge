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


def test_db_clean(db_env):
    """测试全部跑在临时库；真实 mathforge.db 应保持 attempt_events 0 行。"""
    import sqlite3
    real = sqlite3.connect(os.path.join(ROOT, "mathforge.db"))
    n = real.execute("SELECT COUNT(*) FROM attempt_events").fetchone()[0]
    real.close()
    assert n == 0
