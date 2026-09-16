"""MathForge 上下文组装器 v2（DECISIONS D024/D026）。

预算化上下文（估算 ≤4k token）：只装决策必需的最小记忆视图，避免把整库塞进 prompt。
两层 API：
  build_context(conn, pack, kp_id, strategy=None, mem=None) -> dict        # 给人/LLM 看的上下文
  prepare_decision_state(conn, pack, kp_id, difficulty, strategy=None, mem=None) -> dict  # 给 policy2.decide

记忆层由 mem2.py 提供（T2 已实现，接口对齐其源码）：
  mem2.project_mastery(conn, kp, at=None, strategy=None) -> dict|None
        dict 含 {value, decayed_value, n, last_ts}
  mem2.project_all(conn, strategy=None) -> dict
        {kp: {mastery, decayed_value, n, last_ts, streak_correct, last_attribution(dict|None)}}
  mem2.project_mistakes(conn, pack=None, kp=None, limit=20) -> list[dict]

mem2 缺失（import 失败）时全部降级：字段置 None / 空，并标注 _degraded=True。
注意：本模块修复 v1 P1 死代码——assembler 真正解析 prerequisites 并写入 state，
使 decide 首次获得 prerequisites / first_contact 真实输入。
"""

from __future__ import annotations

import re

import pack_loader

try:  # mem2 由另一 agent 实现；缺失则降解，集成时联调
    import mem2 as _MEM2
except Exception:  # noqa: BLE001
    _MEM2 = None


def _safe(meth, default, *args, **kwargs):
    """调用 mem2 方法，异常兜底返回 default（不阻塞决策）。"""
    if _MEM2 is None and meth is None:
        return default
    try:
        return meth(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return default


def _mastery_value(d):
    """兼容 mem2 返回 dict（取 decayed_value）或裸 float / None。"""
    if isinstance(d, dict):
        return d.get("decayed_value")
    return d


def _attrib_type(attr):
    """last_attribution 可能是 {type, conf} 或字符串；统一提取 type 字符串。"""
    if attr is None:
        return None
    if isinstance(attr, dict):
        return attr.get("type")
    return attr


def _attrib_conf(attr):
    """提取归因置信度（None=未知/legacy 数据，不参与门控）。"""
    if isinstance(attr, dict):
        conf = attr.get("conf")
        return float(conf) if isinstance(conf, (int, float)) else None
    return None


def build_context(conn, pack: dict, kp_id: str, strategy: dict | None = None,
                  mem=None) -> dict:
    """预算化上下文（≤4k token）：kp 元信息 + 前置掌握度 + 当前掌握度 + 最近错题(≤3)。"""
    mem = mem if mem is not None else _MEM2
    pack_id = pack["manifest"]["id"]
    kp = pack_loader.get_kp(pack, kp_id) or {}
    prereqs = pack_loader.resolve_prereqs(pack, kp_id)

    prereq_mastery = {}
    for pre in prereqs:
        prereq_mastery[pre] = _safe(
            getattr(mem, "project_mastery", None), None, conn, pre, None, strategy
        )
        prereq_mastery[pre] = _mastery_value(prereq_mastery[pre])

    mastery_current = None
    if mem is not None:
        mastery_current = _mastery_value(
            _safe(getattr(mem, "project_mastery", None), None, conn, kp_id, None, strategy)
        )
    recent = _safe(
        getattr(mem, "project_mistakes", None), [], conn, pack_id, kp_id, 3
    )
    if not isinstance(recent, list):
        recent = []

    return {
        "kp": {
            "id": kp.get("id"),
            "name": kp.get("name"),
            "section": kp.get("section"),
            "difficulty_floor": kp.get("difficulty_floor"),
            "typical_forms": kp.get("typical_forms", []),
        },
        "prereq_mastery": prereq_mastery,
        "mastery_current": mastery_current,
        "recent_mistakes": recent[:3],
        "_degraded": mem is None,
    }


def prepare_decision_state(conn, pack: dict, kp_id: str, difficulty: str,
                           strategy: dict | None = None, mem=None, steps: int = 1) -> dict:
    """供 policy2.decide 的完整 state。

    mem：可注入 mem2 兼容对象（测试用）；默认取模块级 _MEM2（缺失则降解）。
    —— 真实 prerequisites 在此解析并写入 state，修复 v1 P1 死代码。
    """
    mem = mem if mem is not None else _MEM2
    pack_id = pack["manifest"]["id"]
    prereqs = pack_loader.resolve_prereqs(pack, kp_id)

    if mem is not None:
        allp = _safe(getattr(mem, "project_all", None), {}, conn, strategy) or {}
        cur = allp.get(kp_id)
        mastery_decayed = _mastery_value(cur) if isinstance(cur, dict) else (
            cur if isinstance(cur, (int, float)) else None)
        streak = cur.get("streak_correct", 0) if isinstance(cur, dict) else 0
        attrib = _attrib_type(cur.get("last_attribution")) if isinstance(cur, dict) else None
        attrib_conf = _attrib_conf(cur.get("last_attribution")) if isinstance(cur, dict) else None
        first = kp_id not in allp
        prereq_mastery = {}
        for pre in prereqs:
            p = allp.get(pre)
            prereq_mastery[pre] = _mastery_value(p) if isinstance(p, dict) else (
                p if isinstance(p, (int, float)) else None)
        degraded = False
    else:
        mastery_decayed = None
        streak = 0
        attrib = None
        attrib_conf = None
        first = True
        prereq_mastery = {pre: None for pre in prereqs}
        degraded = True

    if mastery_decayed is None:
        mastery_decayed = 0.5  # 未知 kp 默认（对齐 v1 db.get_mastery 兜底）

    return {
        "kp_id": kp_id,
        "pack_id": pack_id,
        "mastery_decayed": mastery_decayed,
        "prereq_mastery": prereq_mastery,
        "first_contact": first,
        "last_wrong_attribution": attrib,
        "last_wrong_attribution_conf": attrib_conf,
        "streak_correct": streak,
        "difficulty": difficulty,
        "steps": steps,
        "conn": conn,
        "_degraded": degraded,
    }
