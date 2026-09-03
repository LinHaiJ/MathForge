"""今日复习清单 + 遗忘可视化（Day6 任务 3）。

- 排序：遗忘后掌握度（db.decayed，半衰期 7 天）低者优先 × 距上次练习久者优先
- 建议动作：基础回炉 / 概念变式 / 重练+变式 / 重练（与 P1-P6 策略语言一致）
- 折线数据：policy_log 里每次决策的记忆快照（mastery_decayed 随时间演化）+ 当前值
- 复用 db.py 记忆三表，不新增表
"""

from __future__ import annotations

import json

import db


def today_list(conn, limit: int = 20, now_ts: float | None = None) -> list[dict]:
    """待复习 kp 清单：刚练完且记忆犹新（<0.5 天且遗忘后仍 ≥60%）的不进清单。"""
    now_ts = now_ts if now_ts is not None else db.now()
    items = []
    for row in conn.execute("SELECT * FROM mastery"):
        d = dict(row)
        decayed = db.decayed(d["mastery"], d["updated_at"], now_ts)
        days = max(0.0, (now_ts - d["updated_at"]) / 86400.0)
        if days < 0.5 and decayed >= 0.55:
            continue
        if decayed < 0.4 or d["wrong_count"] > d["correct_count"]:
            action = "基础回炉"
        elif d["last_wrong_attribution"] == "概念混淆":
            action = "概念变式"
        elif d["last_wrong_attribution"] == "计算失误":
            action = "重练+变式"
        else:
            action = "重练"
        reason = f"遗忘后掌握度 {decayed:.0%} · 距上次练习 {days:.1f} 天"
        if d["last_wrong_attribution"]:
            reason += f" · 最近错因：{d['last_wrong_attribution']}"
        items.append({
            "kp": d["kp"], "mastery": round(d["mastery"], 4),
            "mastery_decayed": round(decayed, 4), "days_since": round(days, 2),
            "correct_count": d["correct_count"], "wrong_count": d["wrong_count"],
            "last_wrong_attribution": d["last_wrong_attribution"],
            "suggestion": action, "reason": reason,
        })
    items.sort(key=lambda x: (x["mastery_decayed"], -x["days_since"]))
    return items[:limit]


def kp_history(conn, kp: str) -> list[dict]:
    """kp 掌握度（遗忘后）随时间点列：policy_log 决策快照 + 当前值；相邻同值去重。"""
    pts: list[dict] = []
    for row in conn.execute("SELECT created_at, input_snapshot FROM policy_log ORDER BY id"):
        try:
            snap = json.loads(row["input_snapshot"])
        except (TypeError, ValueError):
            continue
        m = (snap.get("mastery") or {}).get(kp)
        if isinstance(m, dict) and m.get("mastery_decayed") is not None:
            pts.append({"t": row["created_at"], "v": m["mastery_decayed"]})
    cur = db.get_mastery(conn, kp)
    if cur:
        pts.append({"t": cur["updated_at"], "v": cur["mastery_decayed"]})
    out: list[dict] = []
    for p in pts:
        if not out or out[-1]["v"] != p["v"]:
            out.append(p)
    return out
