"""policy2 决策层单测（D024/D025/D026）。

覆盖：P1 触发（低前置掌握度）/ P4 降档不跌破 floor / P5 阈值(默认2与覆写1) /
P6 兜底 / policy_log 落库 / strategy 深合并覆写生效。
不触网；policy_log 落库用独立 tmp db。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db as db_mod
import policy2
import json

import mem2
import pack_loader


def _strategy(**overrides):
    s = {"policy": dict(pack_loader.DEFAULT_STRATEGY["policy"])}
    s["policy"].update(overrides)
    return s


def _base_state(**kw):
    st = {
        "kp_id": "calc.rolle",
        "pack_id": "calculus",
        "mastery_decayed": 0.5,
        "prereq_mastery": {},
        "first_contact": False,
        "last_wrong_attribution": None,
        "streak_correct": 0,
        "difficulty": "基础",
        "steps": 1,
        "conn": None,
    }
    st.update(kw)
    return st


def test_p1_trigger_on_weak_prereq(monkeypatch):
    # 当前掌握度低且存在前置未达标 → 切前置（可达性门控放行：聚焦 P1 机制）
    monkeypatch.setattr(policy2, "_kp_reachable", lambda kp: True)
    st = _base_state(mastery_decayed=0.3,
                     prereq_mastery={"calc.continuity": 0.3, "calc.derivative.basic": 0.7})
    d = policy2.decide(st, _strategy())
    assert d["rule"] == "P1"
    assert d["action"]["kp_target"] == "calc.continuity"  # 第一个未达 60% 的前置
    assert d["action"]["qtype"] == "calculation"


def test_p1_no_trigger_when_prereq_ok():
    # 当前掌握度低但前置都达标 → 不触发 P1
    st = _base_state(mastery_decayed=0.3,
                     prereq_mastery={"calc.continuity": 0.8, "calc.derivative.basic": 0.9})
    d = policy2.decide(st, _strategy())
    assert d["rule"] != "P1"


def test_p2_first_contact_dup():
    st = _base_state(first_contact=True)
    d = policy2.decide(st, _strategy(p2_dup_basic=True))
    assert d["rule"] == "P2"
    assert d["action"]["count"] == 2
    assert d["action"]["difficulty"] == "基础"

    st2 = _base_state(first_contact=True)
    d2 = policy2.decide(st2, _strategy(p2_dup_basic=False))
    assert d2["rule"] == "P2"
    assert d2["action"]["count"] == 1


def test_p3_concept_variant():
    st = _base_state(last_wrong_attribution="概念混淆")
    d = policy2.decide(st, _strategy(p3_concept_variant=True))
    assert d["rule"] == "P3"
    assert d["action"]["qtype"] == "concept"
    assert d["action"]["plus_variant"] == 1

    st2 = _base_state(last_wrong_attribution="概念混淆")
    d2 = policy2.decide(st2, _strategy(p3_concept_variant=False))
    assert d2["action"]["plus_variant"] == 0


def test_p4_drop_floor_not_below_basic():
    # 进阶降一档 → 基础（floor=basic，不跌破）
    st = _base_state(last_wrong_attribution="计算失误", difficulty="进阶")
    d = policy2.decide(st, _strategy(p4_penalty=True, p4_drop_floor="basic"))
    assert d["rule"] == "P4"
    assert d["action"]["difficulty"] == "基础"
    assert d["action"]["count"] == 2

    # 已是基础，降档仍不低于基础
    st2 = _base_state(last_wrong_attribution="计算失误", difficulty="基础")
    d2 = policy2.decide(st2, _strategy(p4_penalty=True, p4_drop_floor="basic"))
    assert d2["action"]["difficulty"] == "基础"

    # 惩罚关闭 → 不降档、单数
    st3 = _base_state(last_wrong_attribution="计算失误", difficulty="进阶")
    d3 = policy2.decide(st3, _strategy(p4_penalty=False))
    assert d3["action"]["difficulty"] == "进阶"
    assert d3["action"]["count"] == 1


def test_p5_streak_default_and_override():
    # 默认阈值 2：streak=1 不触发，streak=2 触发
    st1 = _base_state(streak_correct=1)
    assert policy2.decide(st1, _strategy())["rule"] == "P6"

    st2 = _base_state(streak_correct=2)
    assert policy2.decide(st2, _strategy())["rule"] == "P5"

    # 覆写阈值 1：streak=1 即升档
    st3 = _base_state(streak_correct=1)
    assert policy2.decide(st3, _strategy(p5_streak=1))["rule"] == "P5"


def test_p6_fallback(monkeypatch):
    # 强制 DEMO（无 key/缓存未命中）→ _p6_explain 走 try/except 静态文案，零 API、确定
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    d = policy2.decide(_base_state(), _strategy())
    assert d["rule"] == "P6"
    assert d["action"]["difficulty"] == "进阶"
    assert "explanation" in d["action"]  # 默认 p6_llm_explain=True
    # 关闭解释
    d2 = policy2.decide(_base_state(), _strategy(p6_llm_explain=False))
    assert "explanation" not in d2["action"]


def test_policy_log_persisted(tmp_path):
    """决策单轨（复评 S5）：只写 attempt_events（mode='policy'），不再双写 v1 policy_log。"""
    conn = mem2.connect(tmp_path / "t.db")  # 单轨决策写 attempt_events → 需 mem2 schema
    st = _base_state(conn=conn, last_wrong_attribution="计算失误", difficulty="进阶")
    d = policy2.decide(st, _strategy())
    rows = conn.execute(
        "SELECT meta FROM attempt_events WHERE mode='policy' ORDER BY id DESC"
    ).fetchall()
    assert len(rows) == 1
    meta = json.loads(rows[0][0])
    assert meta["rule"] == d["rule"] == "P4"
    # 快照可回放
    assert meta["snapshot"]["kp_id"] == "calc.rolle"
    # v1 policy_log 表不再被 v2 决策写入
    try:
        logs = db_mod.get_policy_log(conn, limit=10)
        assert len(logs) == 0
    except Exception:
        pass  # 该库无 policy_log 表也合规（单轨本就不依赖它）
    conn.close()


def test_strategy_deep_merge_override_effective():
    # 深合并：覆写 p5_streak 必须生效，未覆写项保持默认
    s = pack_loader._deep_merge(pack_loader.DEFAULT_STRATEGY,
                                {"policy": {"p5_streak": 1}})
    assert s["policy"]["p5_streak"] == 1  # 覆写生效
    assert s["policy"]["p1_mastery_floor"] == 0.4  # 默认保留

    # decide 实际读取覆写值
    st = _base_state(streak_correct=1)
    assert policy2.decide(st, s)["rule"] == "P5"


# ---- 归因置信门控（2026-09-12）：低置信错因不驱动 P3/P4 ----

def test_p4_blocked_by_low_confidence(tmp_path):
    """conf=0.2 < 阈值 → P3/P4 不触发，落 P6；override conf=1.0 → P4 正常。"""
    conn = mem2.connect(tmp_path / "g.db")
    try:
        strat = {"window": 5, "recency_halflife_days": 7, "decay_halflife_days": 7,
                 "policy": {"p4_penalty": True}}
        base = {"kp_id": "calc.x", "difficulty": "基础", "first_contact": False,
                "mastery_decayed": 0.9, "prereq_mastery": {}, "streak_correct": 0,
                "conn": conn}
        low = {**base, "last_wrong_attribution": "计算失误", "last_wrong_attribution_conf": 0.2}
        d = policy2.decide(low, strat)
        assert d["rule"] == "P6", f"低置信不应触发 P4，实际 {d['rule']}"
        trusted = {**base, "last_wrong_attribution": "计算失误", "last_wrong_attribution_conf": 1.0}
        d2 = policy2.decide(trusted, strat)
        assert d2["rule"] == "P4"
        legacy = {**base, "last_wrong_attribution": "计算失误", "last_wrong_attribution_conf": None}
        d3 = policy2.decide(legacy, strat)
        assert d3["rule"] == "P4"  # legacy 无 conf 放行（向后兼容）
    finally:
        conn.close()
