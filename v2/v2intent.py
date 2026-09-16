"""v2 意图解析（规则版 · 零 LLM）——两页 UI 顶部意图条后端。

语义（用户定案）：意图条只做「变式生题」，不做自由聊天。解析一句话 → 槽位
(kp, 题型, 难度)；kp 必须在 kp_graph 内。命中 → 由调用方（v2api /v2/variant）
以该 kp 最近源题/家族实例生成变式；无 kp 或低置信 → 回退推荐清单，不生成。

规则版设计说明：用 kp 名/典型形态/章节/别名做包含匹配 + 难度/题型词槽解析，
零 API 成本；若 20 例验收集命中率 <18/20，再由主 agent 决定是否挂 LLM 槽位
解析开关（本模块函数签名预留 llm=False 参数位）。
"""

from __future__ import annotations

import pack_loader

# 难度词 → 档位
_DIFF_MAP = [
    (("基础", "入门", "简单", "容易"), "基础"),
    (("进阶", "中等", "中档", "提高"), "进阶"),
    (("综合", "压轴", "困难", "难题", "最难"), "综合"),
]
_DIFF_DEFAULT = "基础"

# 题型词 → v2 qtype（只有填空/解答两类出题，选择/概念题不在 v2 出题通道）
_QTYPE_MAP = [
    (("解答", "大题", "证明", "计算过程"), "solution"),
    (("填空", "求值", "算一下"), "fill"),
]
_QTYPE_DEFAULT = "fill"

# 命中优先级：kp.name 精确包含 > typical_forms 包含 > section 包含
_PRI_NAME, _PRI_FORM, _PRI_SECTION, _PRI_ALIAS = 4, 3, 2, 1

_ALIASES = {
    # 口语/别称 → kp id 候选（可选补充，规则匹配不到时才人工补）
    "罗尔": "calc.rolle",
    "拉格朗日": "calc.lagrange",
    "中值定理": ["calc.lagrange", "calc.rolle"],
    "行列式": "la.det.calc",
    "特征值": "la.eigen",
    "特征向量": "la.eigen",
    "矩阵的逆": "la.matrix.basic",
    "逆矩阵": "la.matrix.basic",
    "二重积分": "calc.double.int",
    "分部积分": "calc.integral.byparts",
    "极限": "calc.limit.basic",
    "洛必达": "calc.limit.lhopital",
    "渐近线": "calc.asymptote",
    "期望": "prob.expectation",
    "方差": "prob.expectation",
    "贝叶斯": "prob.total_bayes",
    "全概率": "prob.total_bayes",
    "概率": "prob.classical",
}

_packs_cache: list[dict] | None = None


def _all_packs() -> list[dict]:
    global _packs_cache
    if _packs_cache is None:
        _packs_cache = [pack_loader.load_pack(p) for p in pack_loader.list_packs()]
    return _packs_cache


def zero_api_kp_ids() -> set[str]:
    """demo/离线可零 API 出题的 kp：包内存在确定性族，或 v1 root 家族（中文名∈_FAMILY_KP）。"""
    from generate import _FAMILY_KP  # 延迟 import，避免环
    out: set[str] = set()
    for pack in _all_packs():
        fam_ids = {getattr(m, "KP_ID", None) for m in pack["families"].values()}
        for k in pack["kp_graph"]:
            if k["id"] in fam_ids or (k.get("name") or "") in _FAMILY_KP:
                out.add(k["id"])
    return out


def recommend_start(limit: int = 3) -> list[dict]:
    """空态推荐：基础档 + 零 API（家族）kp，包权重序（calculus→linear→probability）。"""
    zero = zero_api_kp_ids()
    order = {"calculus": 0, "linear": 1, "probability": 2}
    rows = []
    for pack in _all_packs():
        for k in pack["kp_graph"]:
            if k["id"] not in zero:
                continue
            if k.get("difficulty_floor") != "basic":
                continue
            rows.append({
                "kp": k["id"], "name": k["name"], "pack_id": pack["manifest"]["id"],
                "section": k.get("section"),
            })
    rows.sort(key=lambda r: (order.get(r["pack_id"], 9), -(r.get("section") or "").count("/")))
    return rows[:limit]


def _slot(text: str, table, default):
    for words, val in table:
        if any(w in text for w in words):
            return val
    return default


def _score_kp(text: str, k: dict) -> tuple[int, int]:
    """返回 (优先级, 匹配串长度)，不命中返回 None。"""
    name = k.get("name") or ""
    best: tuple[int, int] | None = None
    if name and name in text:
        best = (_PRI_NAME, len(name))
    else:
        for form in (k.get("typical_forms") or []):
            if form and form in text and len(form) > 1:
                cand = (_PRI_FORM, len(form))
                if best is None or cand[1] > best[1]:
                    best = cand
                break
        if best is None:
            sec = (k.get("section") or "").split("/")[-1]
            if len(sec) > 1 and sec in text:
                best = (_PRI_SECTION, len(sec))
    return best


def parse(text: str) -> dict:
    """解析一句话 → 槽位结果。返回统一结构：
    {ok, slots:{kp_id,kp_name,pack_id,qtype,difficulty}, confidence: high|mid|low,
     second?:{...}, reason}。ok=false 表示无 kp 命中或低置信（调用方必须回退推荐，不生成）。
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "confidence": "low", "reason": "empty"}

    # 1) 别名直配（最高优先且确定）
    alias_hits = []
    for word, target in _ALIASES.items():
        if word in text:
            for kp_id in (target if isinstance(target, list) else [target]):
                for pack in _all_packs():
                    k = pack_loader.get_kp(pack, kp_id)
                    if k is not None:
                        alias_hits.append((_PRI_ALIAS + 3, len(word), pack, k))
    if alias_hits:
        alias_hits.sort(key=lambda x: (-x[0], -x[1]))
        _, _, pack, k = alias_hits[0]
        dif = _slot(text, _DIFF_MAP, _DIFF_DEFAULT)
        qt = _slot(text, _QTYPE_MAP, _QTYPE_DEFAULT)
        return {"ok": True, "slots": {"kp_id": k["id"], "kp_name": k["name"],
                                      "pack_id": pack["manifest"]["id"],
                                      "qtype": qt, "difficulty": dif},
                "confidence": "high", "reason": "alias"}

    # 2) 图内匹配打分
    scored = []
    for pack in _all_packs():
        for k in pack["kp_graph"]:
            s = _score_kp(text, k)
            if s:
                scored.append((s[0], s[1], pack, k))
    if scored:
        scored.sort(key=lambda x: (-x[0], -x[1]))
        pri, ln, pack, k = scored[0]
        if pri >= _PRI_FORM or ln >= 2:
            dif = _slot(text, _DIFF_MAP, _DIFF_DEFAULT)
            qt = _slot(text, _QTYPE_MAP, _QTYPE_DEFAULT)
            conf = "high" if pri >= _PRI_NAME else "mid"
            out = {"ok": True, "slots": {"kp_id": k["id"], "kp_name": k["name"],
                                         "pack_id": pack["manifest"]["id"],
                                         "qtype": qt, "difficulty": dif},
                   "confidence": conf, "reason": f"match:{pri}"}
            if len(scored) > 1 and scored[1][0] >= _PRI_FORM:
                _, _, p2, k2 = scored[1]
                out["second"] = {"kp_id": k2["id"], "kp_name": k2["name"],
                                 "pack_id": p2["manifest"]["id"]}
            return out

    # 3) 无 kp 命中 → 回退（调用方给推荐清单，不生成）
    return {"ok": False, "confidence": "low", "reason": "no_kp"}
