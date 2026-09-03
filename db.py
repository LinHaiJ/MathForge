"""MathForge 记忆三表（PRD §5 ima 四层极简同构）。

mastery(掌握度,半衰期7天) / mistakes(错题+归因) / policy_log(决策轨迹)。
答题即时写入；冲突取最近 N 次高权重（v1 用最近一次作答刷新 mastery）。
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "mathforge.db"
HALF_LIFE_DAYS = 7.0  # PRD §4 经验参数[待评测校准]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mastery (
    kp TEXT PRIMARY KEY,
    mastery REAL NOT NULL DEFAULT 0.5,      -- 0~1，最近一次作答后的掌握度
    correct_count INTEGER NOT NULL DEFAULT 0,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    streak_correct INTEGER NOT NULL DEFAULT 0,  -- 连对计数（P5 依据）
    last_wrong_attribution TEXT,                 -- 最近一次错误归因（P3/P4 依据）
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mistakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kp TEXT NOT NULL,
    question_id TEXT,
    student_answer TEXT,
    standard_answer TEXT,
    attribution TEXT,            -- 概念混淆/计算失误/方法选错/审题错误
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS policy_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    trigger_rule TEXT NOT NULL,          -- P1..P6
    input_snapshot TEXT NOT NULL,        -- JSON：决策时的记忆状态快照
    output TEXT NOT NULL                 -- JSON：决策输出
);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def now() -> float:
    return time.time()


def decayed(mastery: float, updated_at: float, at: float | None = None) -> float:
    """半衰期 7 天遗忘：mastery * 0.5 ** (经过天数 / 7)。"""
    t = at if at is not None else now()
    days = max(0.0, (t - updated_at) / 86400.0)
    return mastery * math.pow(0.5, days / HALF_LIFE_DAYS)


def get_mastery(conn: sqlite3.Connection, kp: str, at: float | None = None) -> dict | None:
    row = conn.execute("SELECT * FROM mastery WHERE kp=?", (kp,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["mastery_decayed"] = round(decayed(d["mastery"], d["updated_at"], at), 4)
    return d


def record_answer(conn: sqlite3.Connection, kp: str, correct: bool,
                  attribution: str | None = None, question_id: str | None = None,
                  student_answer: str | None = None, standard_answer: str | None = None) -> dict:
    """答题即时写入（mastery 刷新 + 错题落 mistakes）。返回刷新后的 mastery 行。"""
    ts = now()
    if not correct:
        conn.execute(
            "INSERT INTO mistakes (kp, question_id, student_answer, standard_answer, attribution, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (kp, question_id, student_answer, standard_answer, attribution, ts))
    row = conn.execute("SELECT * FROM mastery WHERE kp=?", (kp,)).fetchone()
    if row is None:
        m = 0.6 if correct else 0.3
        streak = 1 if correct else 0
        conn.execute(
            "INSERT INTO mastery (kp, mastery, correct_count, wrong_count, streak_correct, last_wrong_attribution, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (kp, m, int(correct), 0 if correct else 1, streak, None if correct else attribution, ts))
    else:
        # 冲突取最近：最新作答直接覆盖掌握度（最近 N 次高权重的 N=1 简化，PRD §5）
        base = decayed(row["mastery"], row["updated_at"], ts)
        m = min(1.0, base + 0.25) if correct else max(0.0, base - 0.3)
        streak = row["streak_correct"] + 1 if correct else 0
        conn.execute(
            "UPDATE mastery SET mastery=?, correct_count=correct_count+?, wrong_count=wrong_count+?, "
            "streak_correct=?, last_wrong_attribution=?, updated_at=? WHERE kp=?",
            (m, int(correct), 0 if correct else 1, streak,
             row["last_wrong_attribution"] if correct else attribution, ts, kp))
    conn.commit()
    return get_mastery(conn, kp)


def update_last_attribution(conn: sqlite3.Connection, kp: str, attribution: str) -> bool:
    """修正该 kp 最近一次错题的归因（人工修正闭环，不重复计分）。

    Day6 题库直刷流用：同步更新 mistakes 最新一行与 mastery.last_wrong_attribution。
    """
    row = conn.execute(
        "SELECT id FROM mistakes WHERE kp=? ORDER BY id DESC LIMIT 1", (kp,)).fetchone()
    if row is None:
        return False
    conn.execute("UPDATE mistakes SET attribution=? WHERE id=?", (attribution, row["id"]))
    conn.execute(
        "UPDATE mastery SET last_wrong_attribution=? WHERE kp=?", (attribution, kp))
    conn.commit()
    return True


def log_policy(conn: sqlite3.Connection, trigger_rule: str, input_snapshot: dict, output: dict) -> int:
    cur = conn.execute(
        "INSERT INTO policy_log (created_at, trigger_rule, input_snapshot, output) VALUES (?,?,?,?)",
        (now(), trigger_rule, json.dumps(input_snapshot, ensure_ascii=False),
         json.dumps(output, ensure_ascii=False)))
    conn.commit()
    return cur.lastrowid


def get_policy_log(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM policy_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["input_snapshot"] = json.loads(d["input_snapshot"])
        d["output"] = json.loads(d["output"])
        out.append(d)
    return out
