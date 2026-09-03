"""MathForge 策略引擎（PRD §4 规则表 v0.1：按优先级命中即执行，v1 无自由裁量）。

规则管调度 / LLM 管生成理解 / SymPy 管验证——本模块是第一层的决策核心。
P6 兜底触发一次 LLM 生成策略解释与建议（走缓存）。
"""

from __future__ import annotations

import json

import db
from llm import chat_json

DIFFICULTY_LADDER = ["基础", "进阶", "综合"]  # PRD §4 三档


def _shift(difficulty: str, steps: int) -> str:
    i = DIFFICULTY_LADDER.index(difficulty) if difficulty in DIFFICULTY_LADDER else 0
    return DIFFICULTY_LADDER[max(0, min(len(DIFFICULTY_LADDER) - 1, i + steps))]


def _snapshot(state: dict) -> dict:
    """决策输入快照（可 JSON 序列化，落 policy_log 供策略台回放）。"""
    return {
        "current_kp": state.get("current_kp"),
        "current_difficulty": state.get("current_difficulty", "基础"),
        "mastery": {kp: db.get_mastery(state["conn"], kp) for kp in state.get("watch_kps", [state.get("current_kp")]) if kp},
        "prerequisites": state.get("prerequisites", {}),
        "first_contact": state.get("first_contact", {}),
    }


def decide(state: dict) -> dict:
    """输入记忆状态 → 输出下一组题决策（知识点×题型×难度×数量+触发规则）。

    state 需含：conn(SQLite)、current_kp、current_difficulty、
    可选 watch_kps / prerequisites{kp:[前置]} / first_contact{kp:bool}。
    """
    conn = state["conn"]
    kp = state["current_kp"]
    diff = state.get("current_difficulty", "基础")
    snap = _snapshot(state)
    m = db.get_mastery(conn, kp) or {"mastery_decayed": 0.5, "streak_correct": 0,
                                     "last_wrong_attribution": None}
    prereqs = state.get("prerequisites", {}).get(kp, [])
    first = state.get("first_contact", {}).get(kp, db.get_mastery(conn, kp) is None)

    rule, action = "P6", None

    # P1 掌握度<40% 且前置知识点未达 60% → 切前置知识点
    if m["mastery_decayed"] < 0.40:
        weak_prereq = None
        for pre in prereqs:
            pm = db.get_mastery(conn, pre)
            if pm is None or pm["mastery_decayed"] < 0.60:
                weak_prereq = pre
                break
        if weak_prereq:
            rule = "P1"
            action = {"kp": weak_prereq, "qtype": "calculation",
                      "difficulty": diff, "count": 1,
                      "reason": f"掌握度 {m['mastery_decayed']:.0%}<40% 且前置「{weak_prereq}」未达 60%"}

    # P2 知识点首次接触 → 基础难度 ×2
    if rule == "P6" and first:
        rule = "P2"
        action = {"kp": kp, "qtype": "calculation", "difficulty": "基础", "count": 2,
                  "reason": "知识点首次接触，基础难度双题探测"}

    # P3 答错·概念混淆 → 概念判断 ×1 + 变式 ×1
    if rule == "P6" and m.get("last_wrong_attribution") == "概念混淆":
        rule = "P3"
        action = {"kp": kp, "qtype": "concept", "difficulty": diff, "count": 1,
                  "plus_variant": 1, "reason": "最近错因=概念混淆，概念判断+变式各一"}

    # P4 答错·计算失误 → 数值变式 ×2（降一档，验收口径"按 P4 降难度"）
    if rule == "P6" and m.get("last_wrong_attribution") == "计算失误":
        rule = "P4"
        action = {"kp": kp, "qtype": "calculation", "difficulty": _shift(diff, -1),
                  "count": 2, "reason": f"最近错因=计算失误，数值变式×2 并降档（{diff}→{_shift(diff, -1)}）"}

    # P5 连对 ≥2 → 难度上移一档
    if rule == "P6" and m.get("streak_correct", 0) >= 2:
        rule = "P5"
        action = {"kp": kp, "qtype": "calculation", "difficulty": _shift(diff, +1), "count": 1,
                  "reason": f"连对 {m['streak_correct']} 次，难度上移（{diff}→{_shift(diff, +1)}）"}

    # P6 兜底：维持当前知识点中等难度 ×1 + LLM 策略解释（M2）
    if rule == "P6":
        action = {"kp": kp, "qtype": "calculation", "difficulty": "进阶", "count": 1,
                  "reason": "无更高优先级规则命中，维持当前知识点中等难度"}
        action["explanation"] = _p6_explain(snap, action)

    decision = {"trigger_rule": rule, **action}
    db.log_policy(conn, rule, snap, decision)  # 每次决策写 policy_log
    return decision


def _p6_explain(snap: dict, action: dict) -> str:
    """P6 兜底的 LLM 策略解释（一次调用，走缓存；失败返回静态文案不阻塞）。"""
    try:
        d = chat_json(
            [{"role": "system", "content": "你是数学训练策略师。根据记忆状态快照与已定决策，"
                                           "用 ≤2 句中文向学生解释为什么这样安排并给出一条建议。"
                                           "只输出 JSON：{\"explanation\": \"...\"}"},
             {"role": "user", "content": json.dumps({"snapshot": snap, "decision": action},
                                                    ensure_ascii=False)}],
            temperature=0.7, namespace="p6:")
        return d.get("explanation", "")
    except Exception:  # noqa: BLE001 —— 解释是增值项，失败不阻塞决策
        return "当前按薄弱点优先策略维持中等难度练习，完成后可查看掌握度变化。"
