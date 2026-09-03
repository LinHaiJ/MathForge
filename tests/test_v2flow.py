# B3 验收：两页 UI 完整路径集成测试（后端视角）
# 错题(母题) → 清单出现(kp.name+最近题干) → 练一题(/v2/variant 带溯源) →
# 作答(变式答对) → 归因/记忆落事件 → 清单刷新(掌握度变化、last_ok 翻转)
import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("MATHFORGE_DEMO", "1")


@pytest.fixture()
def flow():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["MATHFORGE_DEMO"] = "1"
    os.environ["MATHFORGE_V2_DB"] = path
    from fastapi.testclient import TestClient
    import app as appmod
    cli = TestClient(appmod.app)
    yield cli
    if os.path.exists(path):
        os.unlink(path)


def test_full_path_wrong_to_variant_to_correct(flow):
    cli = flow
    # 1) 空态推荐 → 做母题。确定性选 la.det.calc（行列式族，标量答案便于构造错答）
    reco = cli.get("/v2/recommend-start").json()["items"]
    assert reco, "空态应有推荐"
    mother = next((x for x in reco if x["kp"] == "la.det.calc"), reco[0])
    mother_kp, mother_name = mother["kp"], mother["name"]
    t = cli.post("/v2/turn", json={"kp_id": mother_kp, "qtype": "fill"}).json()
    assert t["ok"]
    q = t["question"]
    std = q.get("answer_sympy") or "1"

    # 2) 答错（student=0，标准答案若非 0；若恰为 0 则答 1）→ 事件落库
    wrong_ans = "1" if std.strip() in ("0", "0/1") else "0"
    wrong = cli.post("/v2/answer", json={
        "pack_id": t["pack_id"], "kp": mother_kp, "qtype": "fill",
        "student_answer": wrong_ans, "standard_answer": std,
        "statement_md": q["statement_md"], "analysis": q.get("analysis") or "",
        "difficulty": "基础",
    }).json()
    assert wrong["ok"] is True and wrong["correct"] is False
    mother_event = wrong["event_id"]

    # 3) 清单刷新：出现该 kp，name 为中文、含最近题干与错标
    rv = cli.get("/v2/review?detail=1").json()
    it = next((x for x in rv["items"] if x["kp"] == mother_kp), None)
    assert it is not None
    assert it["name"] == mother_name
    assert it["recent"] and it["recent"][0]["stmt"], "卡片应有最近错题题干摘要"
    assert it["last_ok"] is False and it["trend_str"] == "错"

    # 4) 练一题 → /v2/variant：有源题 → 变式带溯源区（history + changes + consistency）
    v = cli.post("/v2/variant", json={"kp_id": mother_kp, "qtype": "fill"}).json()
    assert v["ok"], v
    prov = v["provenance"]
    assert prov["source_kind"] == "history" and prov["source_summary"]
    assert prov["changes"] and prov["consistency"] is True   # 变式必须带溯源区块
    vq = v["question"]

    # 5) 变式答对 → 事件落库
    vstd = vq.get("answer_sympy") or "1"
    good = cli.post("/v2/answer", json={
        "pack_id": "calculus" if v["pack_id"] is None else v["pack_id"],
        "kp": mother_kp, "qtype": "fill",
        "student_answer": vstd, "standard_answer": vstd,
        "statement_md": vq["statement_md"], "analysis": vq.get("analysis") or "",
        "difficulty": "基础",
    }).json()
    assert good["ok"] and good["correct"] is True

    # 6) 清单刷新：last_ok 翻转、掌握度上升
    rv2 = cli.get("/v2/review?detail=1").json()
    it2 = next(x for x in rv2["items"] if x["kp"] == mother_kp)
    assert it2["last_ok"] is True
    assert it2["trend_str"].endswith("对")
    assert (it2["mastery"] or 0) > (it["mastery"] or 0)

    # 7) 归因覆盖走 override 事件，不新增作答事件（掌握度 n 不变）
    before_n = it2["n"]
    ov = cli.post("/v2/attribution-override",
                  json={"event_id": mother_event, "type": "计算失误"}).json()
    assert ov["ok"] is True
    rv3 = cli.get("/v2/review?detail=1").json()
    it3 = next(x for x in rv3["items"] if x["kp"] == mother_kp)
    assert it3["n"] == before_n, "override 不应增加作答计数"


def test_ui_contract_no_bare_kp_id_in_review(flow):
    """清单契约：前端只靠 name 渲染（review detail 无裸 id 依赖的展示路径）。"""
    cli = flow
    # 造两条不同 kp 记录
    for kp, ans in [("calc.rolle", "1"), ("calc.integral.byparts", "pi")]:
        t = cli.post("/v2/turn", json={"kp_id": kp, "qtype": "fill"}).json()
        q = t["question"]
        cli.post("/v2/answer", json={
            "pack_id": "calculus", "kp": kp, "qtype": "fill",
            "student_answer": "0", "standard_answer": q.get("answer_sympy") or ans,
            "statement_md": q["statement_md"], "difficulty": "基础",
        })
    rv = cli.get("/v2/review?detail=1").json()
    for it in rv["items"]:
        assert it.get("name") and it["name"] != it["kp"]  # 提供 name 供 UI 展示
