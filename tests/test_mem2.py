"""mem2.py 记忆层 v2 单测（独立 db，绝不触碰仓库 mathforge.db）。

运行：cd D:/腾讯冲刺/作品A/mathforge && D:/Python/Python312/python.exe -m pytest tests/test_mem2.py -q
"""

import sqlite3
import time
import uuid

import pytest

from mathforge import mem2
from mathforge.db import DB_PATH as V1_DB_PATH, decayed as v1_decayed

DAY = 86400.0


def _conn(tmp_path):
    p = tmp_path / f"{uuid.uuid4().hex}.db"
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    mem2.init_schema(conn)
    return conn


def _append(conn, kp="kp1", result="correct", ts=1000.0, **kw):
    return mem2.append_event(
        conn, pack=kw.get("pack", "packA"), kp=kp,
        qtype=kw.get("qtype", "fill"), mode=kw.get("mode", "fill"),
        result=result, ts=ts,
        attribution=kw.get("attribution"),
        user_override=kw.get("user_override"),
        meta=kw.get("meta"),
    )


# 1) schema 幂等 ------------------------------------------------------------
def test_init_schema_idempotent(tmp_path):
    conn = _conn(tmp_path)
    mem2.init_schema(conn)  # 第二次调用不应报错
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(attempt_events)")]
    assert cols[:8] == ["id", "ts", "day", "sim", "pack", "kp", "qtype", "mode"]
    assert "attribution" in cols and "user_override" in cols and "meta" in cols
    pcols = [r["name"] for r in conn.execute("PRAGMA table_info(patterns)")]
    assert "pattern_md" in pcols and "confirmed" in pcols
    # 索引存在
    idx = [r["name"] for r in conn.execute("PRAGMA index_list(attempt_events)")]
    assert any("kp_ts" in i for i in idx)
    conn.close()


# 2) append + project N=1 正确性 -------------------------------------------
def test_append_and_project_n1(tmp_path):
    conn = _conn(tmp_path)
    _append(conn, result="correct", ts=1000.0)
    m = mem2.project_mastery(conn, "kp1", at=1000.0)
    assert m is not None
    assert m["value"] == pytest.approx(1.0)
    assert m["n"] == 1
    assert m["last_ts"] == 1000.0
    assert m["decayed_value"] == pytest.approx(1.0)  # at==last_ts → 无衰减
    conn.close()


# 3) 滑窗 N=5 超过后旧事件被丢弃（同 ts → 等权，期望可手算） ---------------
def test_sliding_window_discards_old(tmp_path):
    conn = _conn(tmp_path)
    strat = {"mastery": {"window": 5, "recency_halflife_days": 7.0},
             "decay_half_life_days": 7.0}
    # 同 ts 使所有权重=1；6 条结果：wrong,然后 5 条 correct/wrong 组合
    # 期望丢弃第 1 条(wrong)，取最后 5 条（ids 2..6）：4 个 wrong + 1 个 correct
    results = ["wrong", "wrong", "wrong", "wrong", "wrong", "correct"]
    for r in results:
        _append(conn, result=r, ts=1000.0)  # 同 ts → 等权，期望可手算
    m = mem2.project_mastery(conn, "kp1", at=1000.0, strategy=strat)
    # 最后 5 条 = 后 5 个结果（ids 2..6）= wrong*4 + correct*1 → 1/5 = 0.2
    assert m["n"] == 5
    assert m["value"] == pytest.approx(0.2)
    # 若未丢弃则会是 1/6≈0.1667；0.2 证明第 1 条被丢弃
    assert m["value"] != pytest.approx(1.0 / 6)
    conn.close()


# 4) 权重衰减数学精确（已知 ts 差值手算） ----------------------------------
def test_weight_decay_exact(tmp_path):
    conn = _conn(tmp_path)
    strat = {"mastery": {"window": 5, "recency_halflife_days": 7.0},
             "decay_half_life_days": 7.0}
    # at=7d：A correct@7d (w=1)，B wrong@0 (age=7d → w=0.5)
    _append(conn, result="correct", ts=7.0 * DAY)
    _append(conn, result="wrong", ts=0.0)
    m = mem2.project_mastery(conn, "kp1", at=7.0 * DAY, strategy=strat)
    # value = (1*1 + 0.5*0)/(1+0.5) = 2/3
    assert m["value"] == pytest.approx(2.0 / 3.0, abs=1e-9)
    conn.close()


# 5) decayed_value 语义对齐 v1 ---------------------------------------------
def test_decayed_value_aligns_v1(tmp_path):
    conn = _conn(tmp_path)
    T0 = 1000.0
    _append(conn, result="correct", ts=T0)
    at = T0 + 7.0 * DAY  # 经过一个半衰期
    m = mem2.project_mastery(conn, "kp1", at=at)
    expected = v1_decayed(1.0, T0, at)  # v1: 1.0 * 0.5**1 = 0.5
    assert m["decayed_value"] == pytest.approx(expected)
    assert m["decayed_value"] == pytest.approx(0.5)
    # 不衰减情形
    m0 = mem2.project_mastery(conn, "kp1", at=T0)
    assert m0["decayed_value"] == pytest.approx(1.0)
    conn.close()


# 6) override 后 mistakes 归因更新 ------------------------------------------
def test_override_updates_mistakes_attribution(tmp_path):
    conn = _conn(tmp_path)
    eid = _append(conn, result="wrong", ts=1000.0,
                  attribution={"type": "计算失误", "conf": 0.8}, mode="fill")
    mistakes = mem2.project_mistakes(conn, kp="kp1")
    assert len(mistakes) == 1
    assert mistakes[0]["attribution"]["type"] == "计算失误"
    assert mistakes[0]["attribution"].get("overridden") is None

    mem2.override_attribution(conn, eid, "概念混淆", conf=1.0)
    mistakes = mem2.project_mistakes(conn, kp="kp1")
    assert len(mistakes) == 1  # override 不是错题，不新增
    assert mistakes[0]["attribution"]["type"] == "概念混淆"
    assert mistakes[0]["attribution"]["overridden"] is True
    conn.close()


# 6b) user_override=0 视为“无修正”，不污染归因（对齐模拟器写入语义） -------
def test_user_override_zero_ignored(tmp_path):
    conn = _conn(tmp_path)
    _append(conn, result="wrong", ts=1000.0,
            attribution={"type": "概念混淆", "conf": 0.7},
            user_override=0)  # 模拟器对普通事件默认传 0
    mistakes = mem2.project_mistakes(conn, kp="kp1")
    assert len(mistakes) == 1
    attr = mistakes[0]["attribution"]
    assert attr["type"] == "概念混淆"      # 用原始 attribution，而非被 0 覆盖
    assert attr.get("overridden") is None  # 未被当作 override
    conn.close()


# 7) streak_correct（连对 / 中断） -----------------------------------------
def test_streak_correct(tmp_path):
    conn = _conn(tmp_path)

    def build(seq):
        c = _conn(tmp_path)
        t = 0
        for r in seq:
            t += 10.0
            _append(c, result=r, ts=t)
        return c

    # 全连对
    c1 = build(["correct", "correct", "correct"])
    assert mem2.project_all(c1)["kp1"]["streak_correct"] == 3
    # 最近一条非 correct → 0
    c2 = build(["correct", "correct", "wrong"])
    assert mem2.project_all(c2)["kp1"]["streak_correct"] == 0
    # 中断：correct(最新), wrong, correct
    c3 = build(["correct", "wrong", "correct"])
    assert mem2.project_all(c3)["kp1"]["streak_correct"] == 1
    # 空
    c4 = _conn(tmp_path)
    assert "kp1" not in mem2.project_all(c4)
    conn.close()


# 8) patterns set / get ------------------------------------------------------
def test_patterns_set_get(tmp_path):
    conn = _conn(tmp_path)
    mid = mem2.patterns_set(conn, "packA", "kp1",
                            "易混淆极值与驻点", "distill", confirmed=False)
    assert isinstance(mid, int)
    got = mem2.patterns_get(conn, "packA", "kp1")
    assert got["pattern_md"] == "易混淆极值与驻点"
    assert got["source"] == "distill"
    assert got["confirmed"] is False
    # upsert 更新
    mem2.patterns_set(conn, "packA", "kp1", "更新后的模式", "human", confirmed=True)
    got2 = mem2.patterns_get(conn, "packA", "kp1")
    assert got2["pattern_md"] == "更新后的模式"
    assert got2["confirmed"] is True
    # 不同 kp 独立
    assert mem2.patterns_get(conn, "packA", "kp2") is None
    conn.close()


# 9) default_db_path 与 v1 同文件 -------------------------------------------
def test_default_db_path_matches_v1(tmp_path):
    assert mem2.default_db_path() == V1_DB_PATH
    assert mem2.default_db_path().name == "mathforge.db"
