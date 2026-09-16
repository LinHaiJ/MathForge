"""MathForge 决策层 v2（D024/D025/D026 定案）。

策略数据化：strategy 来自 pack_loader 合并结果（DEFAULT_STRATEGY 全局默认 +
每包 strategy.json 深合并），阈值全部参数化。P1-P6 优先级链语义继承自 v1 policy.py。

decide(state, strategy) -> dict
  state 由 assembler.prepare_decision_state 提供，含：
    kp_id(str) / pack_id(str) / mastery_decayed(float) / prereq_mastery(dict: kp_id->decayed|None)
    / first_contact(bool) / last_wrong_attribution(str|None) / streak_correct(int)
    / last_wrong_attribution_conf(float|None)
    / difficulty(str) / steps(int) / conn(sqlite|None, 用于落决策事件)
  strategy 含 strategy['policy'] 阈值子表（见 pack_loader.DEFAULT_STRATEGY）。

输出 {rule, action:{qtype, difficulty, reason, kp_target, count, ...}, snapshot: state}
  —— 决策单轨（复评 S5）：只写 attempt_events mode='policy'，可回放；不再写 v1 policy_log。
"""

from __future__ import annotations

import db
import pack_loader

# v1 三档难度阶梯（PRD §4）
DIFFICULTY_LADDER = ["基础", "进阶", "综合"]
# strategy 中 floor token -> 阶梯标签
_FLOOR_LABEL = {"basic": "基础", "mid": "进阶", "hard": "综合"}

# 合并基准：DEFAULT_STRATEGY.policy 作为所有阈值兜底
_DEFAULT_POLICY = dict(pack_loader.DEFAULT_STRATEGY["policy"])


def _policy(strategy: dict) -> dict:
    """取出 policy 阈值子表，并以全局默认兜底缺失项。"""
    return {**_DEFAULT_POLICY, **(strategy or {}).get("policy", {})}


def _shift(difficulty: str, steps: int) -> str:
    i = DIFFICULTY_LADDER.index(difficulty) if difficulty in DIFFICULTY_LADDER else 0
    return DIFFICULTY_LADDER[max(0, min(len(DIFFICULTY_LADDER) - 1, i + steps))]


def _clamp_floor(difficulty: str, floor_token: str) -> str:
    """降档时不低于 floor_token（basic/mid/hard）。"""
    floor = _FLOOR_LABEL.get(floor_token, "基础")
    lo = DIFFICULTY_LADDER.index(floor) if floor in DIFFICULTY_LADDER else 0
    cur = DIFFICULTY_LADDER.index(difficulty) if difficulty in DIFFICULTY_LADDER else 0
    return DIFFICULTY_LADDER[max(lo, cur)]



def _kp_display(kp_id: str) -> str:
    """kp 显示名（中文 name）；查不到原样返回。UI 红线：理由文案不裸显 kp id。"""
    try:
        pid = pack_loader.find_pack_for_kp(kp_id)
        if pid:
            k = pack_loader.get_kp(pack_loader.load_pack(pid), kp_id)
            if k and k.get("name"):
                return k["name"]
    except Exception:
        pass
    return kp_id


def _kp_reachable(kp_id: str) -> bool:
    """演示模式下只推荐确定性族考点（学生复评：别把学生引向出不了题的死路）。"""
    import os as _os
    if _os.environ.get("MATHFORGE_DEMO") != "1":
        return True
    try:
        for pid in pack_loader.list_packs():
            pk = pack_loader.load_pack(pid)
            for mod in (pk.get("families") or {}).values():
                if getattr(mod, "KP_ID", None) == kp_id:
                    return True
        return False
    except Exception:
        return True


def decide(state: dict, strategy: dict) -> dict:
    """P1-P6 优先级链决策（命中即执行，与 v1 同序：P1>P2>P3>P4>P5>P6）。"""
    pol = _policy(strategy)
    kp = state["kp_id"]
    diff = state.get("difficulty", "基础")
    p1_mastery_floor = pol["p1_mastery_floor"]
    p1_prereq_floor = pol["p1_prereq_floor"]
    p4_drop_floor = pol["p4_drop_floor"]
    p5_streak = pol["p5_streak"]

    m_decayed = state.get("mastery_decayed")
    if m_decayed is None:
        m_decayed = 0.5  # 未知 kp 默认（对齐 v1 db.get_mastery 兜底）
    prereq_mastery = state.get("prereq_mastery", {}) or {}
    first = state.get("first_contact", False)
    attrib = state.get("last_wrong_attribution")
    streak = state.get("streak_correct", 0) or 0

    rule, action = "P6", None

    # P1 掌握度 < floor 且存在前置未达 prereq_floor → 切前置知识点
    if m_decayed < p1_mastery_floor:
        weak_prereq = None
        for pre, pm in prereq_mastery.items():
            if pm is None or pm < p1_prereq_floor:
                weak_prereq = pre
                break
        if weak_prereq and not _kp_reachable(weak_prereq):
            weak_prereq = None  # 前置薄弱但当前模式出不了题 → 不硬推荐，落 P6
        if weak_prereq:
            rule = "P1"
            action = {
                "kp_target": weak_prereq,
                "qtype": "calculation",
                "difficulty": diff,
                "count": 1,
                "reason": f"掌握度 {m_decayed:.0%}<{p1_mastery_floor:.0%} 且前置「{_kp_display(weak_prereq)}」未达 {p1_prereq_floor:.0%}",
            }

    # P2 知识点首次接触 → 基础难度（p2_dup_basic 控制是否双题探测）
    if rule == "P6" and first:
        rule = "P2"
        count = 2 if pol.get("p2_dup_basic", True) else 1
        action = {
            "kp_target": kp,
            "qtype": "calculation",
            "difficulty": "基础",
            "count": count,
            "reason": "知识点首次接触，基础难度探测" + ("×2" if count == 2 else ""),
        }

    # 归因置信门控（2026-09-12）：低置信错因不驱动 P3/P4（68% GT 噪声不进调度阈值）。
    # conf=None 视为 legacy 数据放行；人工 override 恒 1.0。
    p34_conf_floor = float(pol.get("p34_conf_floor", 0.5))
    attrib_conf = state.get("last_wrong_attribution_conf")
    attr_trusted = attrib_conf is None or attrib_conf >= p34_conf_floor

    # P3 答错·概念混淆 → 概念判断 + 变式
    if rule == "P6" and attrib == "概念混淆" and attr_trusted:
        rule = "P3"
        variant = 1 if pol.get("p3_concept_variant", True) else 0
        action = {
            "kp_target": kp,
            "qtype": "concept",
            "difficulty": diff,
            "count": 1,
            "plus_variant": variant,
            "reason": "最近错因=概念混淆，概念判断" + ("+变式" if variant else ""),
        }

    # P4 答错·计算失误 → 数值变式（p4_penalty 控制降档，不低于 p4_drop_floor）
    if rule == "P6" and attrib == "计算失误" and attr_trusted:
        rule = "P4"
        if pol.get("p4_penalty", True):
            new_diff = _clamp_floor(_shift(diff, -1), p4_drop_floor)
            count = 2
            reason = f"最近错因=计算失误，数值变式×2 并降档（{diff}→{new_diff}）"
        else:
            new_diff = diff
            count = 1
            reason = "最近错因=计算失误，数值变式×1（惩罚关闭，不降档）"
        action = {
            "kp_target": kp,
            "qtype": "calculation",
            "difficulty": new_diff,
            "count": count,
            "reason": reason,
        }

    # P5 连对 ≥ p5_streak → 难度上移一档
    if rule == "P6" and streak >= p5_streak:
        rule = "P5"
        new_diff = _shift(diff, +1)
        action = {
            "kp_target": kp,
            "qtype": "calculation",
            "difficulty": new_diff,
            "count": 1,
            "reason": f"连对 {streak} 次（≥{p5_streak}），难度上移（{diff}→{new_diff}）",
        }

    # P6 兜底：维持当前知识点中等难度 ×1 +（可选）LLM 策略解释
    if rule == "P6":
        action = {
            "kp_target": kp,
            "qtype": "calculation",
            "difficulty": "进阶",
            "count": 1,
            "reason": "无更高优先级规则命中，维持当前知识点中等难度",
        }
        if pol.get("p6_llm_explain", True):
            action["explanation"] = _p6_explain(state, action)

    # 快照排除 conn（传输句柄，非决策数据），保证可 JSON 序列化、可回放
    snapshot = {k: v for k, v in state.items() if k != "conn"}
    decision = {"rule": rule, "action": action, "snapshot": snapshot}
    conn = state.get("conn")
    _log_decision(conn, rule, snapshot, decision)  # 每次决策落盘（可回放）
    return decision


def _log_decision(conn, rule: str, snapshot: dict, decision: dict) -> None:
    """决策落盘（单轨，复评 S5）：只写 attempt_events（mode='policy'）。

    此前按「policy_log 表存在与否」双轨回落——默认库有 v1 policy_log，导致
    v2 时间线/当日明细（读 attempt_events）永远为空。现在 v2 决策只写事件流；
    v1 演示线的策略台仍读 v1 policy.py 自己写的 policy_log，互不影响。"""
    if conn is None:
        return
    try:
        import mem2

        mem2.append_event(
            conn,
            pack=(snapshot.get("pack_id") or ""),
            kp=(snapshot.get("kp") or snapshot.get("kp_id") or ""),
            qtype="",
            mode="policy",
            result="policy",
            meta={"rule": rule, "action": decision.get("action"), "snapshot": snapshot},
            sim=1 if snapshot.get("sim") else 0,
        )
    except Exception:
        pass  # 落盘失败不阻断决策（可回放性降级，策略已返回）


def _p6_explain(snap: dict, action: dict) -> str:
    """P6 兜底的 LLM 策略解释（一次调用，走缓存；失败返回静态文案不阻塞）。"""
    from llm import chat_json
    import json

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
