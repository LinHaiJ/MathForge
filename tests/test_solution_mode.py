"""Day6 任务 2：解答题自评对照模式单测。

- 生成引擎解答题（qtype="solution"）：复用绿标验证链（SymPy 构造 + 盲解对账），
  家族路由 kp 零 API 可出（DEMO=1 下盲解失败也不阻断，走 judge_flag 留档）。
- /bank/self-assess 端点：三档自评 → 记忆写入（tmp DB，不污染真实库）。
"""

import os
import sys
from pathlib import Path

os.environ["MATHFORGE_DEMO"] = "1"  # 盲解走缓存，未命中按不一致处理（judge_flag），不出网

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from generate import generate_question  # noqa: E402


def test_generated_solution_question_uses_green_chain():
    q = generate_question({"kp": "罗尔定理"}, difficulty="基础", qtype="solution")
    assert q.get("statement_md"), q.get("error")
    assert q["qtype"] == "solution"
    assert q["verify_level"] == "green"          # 验证链与计算题一致
    assert q.get("answer_sympy")                 # SymPy 推导的标准答案
    assert q.get("analysis")


def test_calculation_qtype_unchanged():
    q = generate_question({"kp": "拉格朗日中值定理"}, difficulty="基础", qtype="calculation")
    assert q.get("statement_md"), q.get("error")
    assert q["qtype"] == "calculation"


def test_self_assess_endpoint_writes_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    from app import app

    client = TestClient(app)
    r = client.post("/bank/self-assess", json={
        "kp": "测试kp", "statement_md": "求 $f'(x)$。", "grade": "不会",
        "standard_answer": "2x", "current_difficulty": "基础"})
    assert r.status_code == 200
    d = r.json()
    assert d["correct"] is False and d["grade"] == "不会"
    assert d["next"]["trigger_rule"]

    r2 = client.post("/bank/self-assess", json={
        "kp": "测试kp", "statement_md": "求 $f'(x)$。", "grade": "会",
        "current_difficulty": "基础"})
    d2 = r2.json()
    assert d2["correct"] is True

    conn = db.connect(tmp_path / "t.db")
    row = conn.execute("SELECT * FROM mastery WHERE kp='测试kp'").fetchone()
    assert row["correct_count"] == 1 and row["wrong_count"] == 1
    mistakes = conn.execute("SELECT * FROM mistakes WHERE kp='测试kp'").fetchall()
    assert len(mistakes) == 1  # 不会 → 记错题本（驱动复习清单）
    conn.close()


def test_self_assess_rejects_bad_grade(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t2.db")
    from app import app

    client = TestClient(app)
    r = client.post("/bank/self-assess", json={"kp": "x", "grade": "满分"})
    assert r.status_code == 400


def test_fix_attribution_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t3.db")
    from app import app

    client = TestClient(app)
    client.post("/bank/self-assess", json={
        "kp": "kp甲", "statement_md": "题", "grade": "不会", "standard_answer": "1"})
    r = client.post("/bank/fix-attribution", json={"kp": "kp甲", "attribution": "计算失误"})
    assert r.status_code == 200
    conn = db.connect(tmp_path / "t3.db")
    m = conn.execute("SELECT last_wrong_attribution FROM mastery WHERE kp='kp甲'").fetchone()
    assert m["last_wrong_attribution"] == "计算失误"
    conn.close()
    # 非四类标签拒绝
    r2 = client.post("/bank/fix-attribution", json={"kp": "kp甲", "attribution": "粗心"})
    assert r2.status_code == 400
