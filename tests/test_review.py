"""Day6 任务 3：今日复习清单 + 遗忘可视化单测（tmp DB，不污染真实库）。"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import review  # noqa: E402

DAY = 86400.0


def _conn(tmp_path, name="r.db"):
    return db.connect(tmp_path / name)


def test_today_list_orders_by_decay_then_staleness(tmp_path):
    conn = _conn(tmp_path)
    # kpA：答对一次但已过 14 天（半衰期两个周期，遗忘重）
    db.record_answer(conn, "kpA", correct=True)
    conn.execute("UPDATE mastery SET updated_at=? WHERE kp='kpA'", (db.now() - 14 * DAY,))
    # kpB：答错一次，10 天前（错多）
    db.record_answer(conn, "kpB", correct=False, attribution="概念混淆")
    conn.execute("UPDATE mastery SET updated_at=? WHERE kp='kpB'", (db.now() - 10 * DAY,))
    # kpC：刚答对（记忆犹新 → 不进清单）
    db.record_answer(conn, "kpC", correct=True)
    conn.commit()

    items = review.today_list(conn)
    kps = [i["kp"] for i in items]
    assert "kpC" not in kps
    assert set(kps) == {"kpA", "kpB"}
    a = next(i for i in items if i["kp"] == "kpA")
    b = next(i for i in items if i["kp"] == "kpB")
    # 遗忘后掌握度更低者在前（kpB 答错起点低且已过 10 天）
    assert b["mastery_decayed"] <= a["mastery_decayed"]
    assert kps[0] == b["kp"]
    # 建议动作与错因联动
    assert b["suggestion"] in ("基础回炉", "概念变式")
    assert "概念混淆" in b["reason"]
    conn.close()


def test_today_list_suggestion_fresh_correct(tmp_path):
    conn = _conn(tmp_path)
    db.record_answer(conn, "kpD", correct=True)
    # 2 天前答对：有遗忘但不错很多 → 重练
    conn.execute("UPDATE mastery SET updated_at=? WHERE kp='kpD'", (db.now() - 2 * DAY,))
    conn.commit()
    items = review.today_list(conn)
    assert len(items) == 1 and items[0]["suggestion"] == "重练"
    conn.close()


def test_kp_history_from_policy_log(tmp_path):
    conn = _conn(tmp_path)
    db.record_answer(conn, "kpE", correct=False, attribution="计算失误")
    decision = {"kp": "kpE", "qtype": "calculation", "difficulty": "基础", "count": 1}
    db.log_policy(conn, "P6", {"mastery": {"kpE": db.get_mastery(conn, "kpE")}}, decision)
    db.record_answer(conn, "kpE", correct=True)
    conn.commit()
    pts = review.kp_history(conn, "kpE")
    assert len(pts) >= 2                    # 快照 + 当前值
    assert pts[-1]["v"] >= pts[0]["v"]      # 答对后掌握度上升
    assert all(0.0 <= p["v"] <= 1.0 for p in pts)
    conn.close()


def test_kp_history_empty_for_unknown_kp(tmp_path):
    conn = _conn(tmp_path)
    assert review.kp_history(conn, "不存在") == []
    conn.close()
