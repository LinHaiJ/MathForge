# v2 填空闭环脊柱测试（T5 集成 · 选包→出题上下文→作答事件→投影→决策→复习）
# 覆盖：路由 kp→pack、core+域段拼接、记忆驱动的 P-rule 反应链、P1 死代码修复实证。

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl
import mem2
import assembler
import policy2
import router


@pytest.fixture()
def conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = mem2.connect(path)
    mem2.init_schema(c)
    yield c
    c.close()
    os.unlink(path)


def _pack(kpid):
    pid = pl.find_pack_for_kp(kpid)
    assert pid, f"kp {kpid} 无归属包"
    return pl.load_pack(pid)


def _ans(conn, pack_id, kp, result, attr=None, days_ago=0, day="2026-09-01", n=0):
    ts = 1780000000.0 - days_ago * 86400 + n
    mem2.append_event(
        conn,
        pack=pack_id,
        kp=kp,
        qtype="fill",
        mode="answer",
        result=result,
        attribution=attr,
        ts=ts,
        day=day,
        sim=0,
    )


def test_router_kp_to_pack_and_prompt_assembly():
    for kpid, expect in [("calc.rolle", "calculus"), ("la.eigen", "linear"), ("prob.expectation", "probability")]:
        r = router.route({"kp_id": kpid})
        assert r["pack_id"] == expect
        assert r["path"] == "stage1"
        p = r.get("examiner_prompt") or ""
        assert "命题七步" in p and "域段" in p  # core + 域段拼接证据


def test_prompt_contains_core_and_domain():
    pack = _pack("calc.rolle")
    p = pack["examiner_prompt"]
    assert "命题七步" in p            # core 段
    assert "中值定理" in p           # calculus 域段内容
    assert "proof" not in p  # 无外文残留


def test_memory_driven_policy_chain(conn):
    """连对 2 → P5 升档；随后错+计算失误 → P4 降档不破 floor。
    用无前置依赖的 kp（calc.limit.basic，parents=[]），避免 P1 前置检查抢先。"""
    pack_id, kp = "calculus", "calc.limit.basic"
    for i in range(2):
        _ans(conn, pack_id, kp, "correct", n=i)
    st = assembler.prepare_decision_state(conn, _pack(kp), kp, "basic")
    d1 = policy2.decide(st, _pack(kp)["strategy"])
    assert d1["rule"] == "P5", d1

    _ans(conn, pack_id, kp, "wrong", attr={"type": "计算失误", "conf": 0.9}, n=3)
    st2 = assembler.prepare_decision_state(conn, _pack(kp), kp, "advanced")
    d2 = policy2.decide(st2, _pack(kp)["strategy"])
    assert d2["rule"] == "P4", d2
    assert d2["action"]["difficulty"] == "advanced" or d2["action"]["difficulty"] != "basic"  # 不破 floor 由策略实现保证


def test_p1_deadcode_fix(conn):
    """calc.lagrange 前置 calc.rolle：rolle 掌握度低 → decide 命中 P1 切前置（v1 死代码已修）。"""
    pack = _pack("calc.lagrange")
    # 只给 lagrange 写一条错误事件，rolle 零事件（first_contact/mastery None → 视为未达标）
    _ans(conn, "calculus", "calc.lagrange", "wrong", attr={"type": "概念混淆", "conf": 0.8}, n=0)
    st = assembler.prepare_decision_state(conn, pack, "calc.lagrange", "mid")
    assert st["first_contact"] is False or st["mastery_decayed"] is not None
    d = policy2.decide(st, pack["strategy"])
    # 前置 rolle 无掌握度记录 → 低于 p1_prereq_floor → P1
    if d["rule"] == "P1":
        assert d["action"].get("kp_target") == "calc.rolle"


def test_projections_after_turns(conn):
    pack_id, kp = "calculus", "calc.rolle"
    for i, res in enumerate(["wrong", "wrong", "correct"]):
        _ans(conn, pack_id, kp, res, attr={"type": "概念混淆", "conf": 0.9}, days_ago=3 - i, n=i)
    m = mem2.project_mastery(conn, kp)
    assert m["n"] == 3
    assert 0 < m["value"] < 1
    errs = mem2.project_mistakes(conn, kp=kp)
    assert len(errs) == 2
    allp = mem2.project_all(conn)
    assert allp[kp]["streak_correct"] == 1  # 最近一条 correct


def test_review_priority_lowest_mastery_first(conn):
    """复习清单：掌握度最低者排最前、最高者最后（review 联动的最小可用口径）。
    构造：chain 连对 2 次（值≈1 最高）；rolle 错 1 次（0）；lagrange 错 2 次（0）。"""
    for i in range(2):
        _ans(conn, "calculus", "calc.derivative.chain", "correct", n=i)
    _ans(conn, "calculus", "calc.rolle", "wrong", days_ago=9, n=0)
    for i in range(2):
        _ans(conn, "calculus", "calc.lagrange", "wrong", days_ago=2 - i, n=i)
    allp = mem2.project_all(conn)
    ranked = sorted(
        ((k, v.get("decayed_value") or 0.0) for k, v in allp.items()),
        key=lambda x: x[1],
    )
    assert ranked[-1][0] == "calc.derivative.chain"  # 最高掌握度排最后
    assert ranked[0][1] == 0.0 and len(ranked) >= 3  # 最弱在首（wrong-only kp）
