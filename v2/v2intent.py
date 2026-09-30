"""v2 意图解析（规则版 · 零 LLM）——两页 UI 顶部意图条后端。

语义（用户定案）：意图条只做「变式生题」，不做自由聊天。解析一句话 → 槽位
(kp, 题型, 难度)；kp 必须在 kp_graph 内。命中 → 由调用方（v2api /v2/variant）
以该 kp 最近源题/家族实例生成变式；无 kp 或低置信 → 回退推荐清单，不生成。

规则版设计说明：用 kp 名/典型形态/章节/别名做包含匹配 + 难度/题型词槽解析，
零 API 成本；若 20 例验收集命中率 <18/20，再由主 agent 决定是否挂 LLM 槽位
解析开关（本模块函数签名预留 llm=False 参数位）。
"""

from __future__ import annotations

import os

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


def _demo_mode() -> bool:
    """demo 判定（口径同 v2/router._demo_mode）：MATHFORGE_DEMO=1 或无 DEEPSEEK_API_KEY。"""
    if os.environ.get("MATHFORGE_DEMO") == "1":
        return True
    return not os.environ.get("DEEPSEEK_API_KEY")


_zero_api_cache: set[str] | None = None


def zero_api_kp_ids() -> set[str]:
    """demo/离线可零 API 出题的 kp：包内存在确定性族，或 v1 root 家族（中文名∈_FAMILY_KP）。

    进程级缓存（与 _packs_cache 同生命周期；测试需要换包场景时先
    monkeypatch.setattr(v2intent, "_zero_api_cache", None) 隔离）。返回副本，
    防调用方误改缓存。"""
    global _zero_api_cache
    if _zero_api_cache is None:
        from generate import _FAMILY_KP  # 延迟 import，避免环
        out: set[str] = set()
        for pack in _all_packs():
            fam_ids = {getattr(m, "KP_ID", None) for m in pack["families"].values()}
            for k in pack["kp_graph"]:
                if k["id"] in fam_ids or (k.get("name") or "") in _FAMILY_KP:
                    out.add(k["id"])
        _zero_api_cache = out
    return set(_zero_api_cache)


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


def _alt(pack: dict, k: dict) -> dict:
    """候选考点统一形（歧义卡/second 共用；UI 禁裸 id，故带中文名）。"""
    return {"kp_id": k["id"], "kp_name": k["name"], "pack_id": pack["manifest"]["id"]}


def _demo_filter(res: dict) -> dict:
    """demo 态确认卡候选可达性过滤（模块 E 复审 major）。

    demo 下确认卡候选只保留 zero_api_kp_ids() 内的 kp——点选后必须真能零 API
    出题（policy2._kp_reachable 红线：别把学生引向出不了题的死路）。过滤后候选
    ≤1 个 → 不再弹卡（ambiguous=false、alternatives=[]）：唯一可达候选直接成为
    最终选择（slots 对齐，学生无感直达）；0 个 → slots 维持首选不动（学生点名了
    不可达 kp，由 /v2/variant 既有 need_mother 引导兜底）。在线模式不过滤
    （LLM 兜底可出题）。过滤后保持不变式：slots 首选 == alternatives[0]。
    """
    if not (res.get("ok") and res.get("ambiguous")) or not _demo_mode():
        return res
    zero = zero_api_kp_ids()
    alts = [a for a in (res.get("alternatives") or []) if a["kp_id"] in zero]
    slots = res["slots"]
    if len(alts) <= 1:
        res["ambiguous"] = False
        res["alternatives"] = []
        res.pop("second", None)          # second 与 alternatives[1] 同构，卡没了随之去除
        if alts:                          # 唯一可达候选即最终选择：slots 与之对齐
            slots["kp_id"] = alts[0]["kp_id"]
            slots["kp_name"] = alts[0]["kp_name"]
            slots["pack_id"] = alts[0]["pack_id"]
        return res
    res["alternatives"] = alts
    if slots.get("kp_id") != alts[0]["kp_id"]:   # 旧首选不可达 → 对齐过滤后首选
        slots["kp_id"] = alts[0]["kp_id"]
        slots["kp_name"] = alts[0]["kp_name"]
        slots["pack_id"] = alts[0]["pack_id"]
    return res


def parse(text: str) -> dict:
    """解析一句话 → 槽位结果。返回统一结构：
    {ok, slots:{kp_id,kp_name,pack_id,qtype,difficulty}, confidence: high|mid|low,
     ambiguous: bool, alternatives: [{kp_id,kp_name,pack_id}, ...], second?:{...},
     reason}。

    歧义定案（模块 E「问而不猜」）：
    - 单候选命中（含单目标别名，如「洛必达」）→ ambiguous=false、alternatives=[]，
      响应除新增两字段外与旧版逐字一致（老用户零感知）；
    - 别名消解先行（复审 blocker 定案）：命中先分「单目标别名命中集」（明确点名，
      如「罗尔」）与「多目标别名命中集」（一词多义，如「中值定理」）。存在单目标
      命中时首选与歧义判定都从单目标命中集出（多目标只作补充候选）——混名
      「罗尔中值定理」→ rolle、不弹卡；单目标命中集自身含多个 kp（如「罗尔
      拉格朗日」同句）才 ambiguous=true；仅多目标命中（「中值定理」）→ ambiguous=true；
    - 图内双高分匹配（第二名优先级 ≥ _PRI_FORM 且与第一名不同 kp）→ ambiguous=true；
    - ambiguous=true 时 ok 仍为 true、confidence 语义不变，alternatives=按现有
      优先级排序的全部候选（截前 3 个），由调用方（v2api /v2/intent → 前端确认卡）
      决定要不要先让学生点选再出题。不变式：有候选时 slots 首选 == alternatives[0]
      （任何输入下不会「首选 A、卡里推荐 B」）；
    - demo 态（MATHFORGE_DEMO=1 或无 DEEPSEEK_API_KEY，口径同 router._demo_mode）
      候选做可达性过滤（复审 major 定案）：alternatives 只保留 zero_api_kp_ids()
      内的 kp（点选后真能出题）；过滤后 ≤1 个 → ambiguous=false、alternatives=[]，
      slots 对齐唯一可达候选（不用再问、直接走）；0 个维持首选（/v2/variant 的
      need_mother 引导兜底）。在线模式不过滤；
    - second 字段为旧版兼容（图内第二名），与 alternatives[1] 同构，保留不删
      （demo 过滤致卡消失时随 alternatives 一并去除）。
    ok=false（无 kp 命中/空句）表示低置信回退，此时 ambiguous=false、alternatives=[]。
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "confidence": "low", "reason": "empty",
                "ambiguous": False, "alternatives": []}

    # 1) 别名直配（最高优先且确定）
    alias_hits = []   # (优先级, 词长, pack, kp, 是否单目标别名)
    for word, target in _ALIASES.items():
        if word in text:
            single = not isinstance(target, list)
            for kp_id in (target if isinstance(target, list) else [target]):
                for pack in _all_packs():
                    k = pack_loader.get_kp(pack, kp_id)
                    if k is not None:
                        alias_hits.append((_PRI_ALIAS + 3, len(word), pack, k, single))
    if alias_hits:
        alias_hits.sort(key=lambda x: (-x[0], -x[1]))
        singles = [h for h in alias_hits if h[4]]    # 单目标别名 = 明确点名
        multis = [h for h in alias_hits if not h[4]]
        # 消解先行（模块 E 复审 blocker）：首选与歧义判定同源——存在单目标命中时，
        # 首选与歧义判定都从单目标命中集出（多目标命中只作补充候选，绝不反向决定
        # 首选）；消解后命中集仍含多个 kp 才算歧义。不变式：ambiguous 时
        # slots.kp_id == alternatives[0].kp_id（首选 A、卡里推荐 B 的情况不存在）。
        pool = singles or multis
        _, _, pack, k, _single = pool[0]
        ambiguous = len({h[3]["id"] for h in pool}) > 1
        alts: list[dict] = []
        if ambiguous:
            for _pri, _ln, p, kk, _s in (pool + multis if singles else pool):
                if all(a["kp_id"] != kk["id"] for a in alts):
                    alts.append(_alt(p, kk))
        dif = _slot(text, _DIFF_MAP, _DIFF_DEFAULT)
        qt = _slot(text, _QTYPE_MAP, _QTYPE_DEFAULT)
        return _demo_filter({
            "ok": True, "slots": {"kp_id": k["id"], "kp_name": k["name"],
                                  "pack_id": pack["manifest"]["id"],
                                  "qtype": qt, "difficulty": dif},
            "confidence": "high", "reason": "alias",
            "ambiguous": ambiguous,
            "alternatives": alts[:3] if ambiguous else []})

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
            # 双高分且不同 kp → 歧义（second 旧字段保留兼容，值并入 alternatives）
            if len(scored) > 1 and scored[1][0] >= _PRI_FORM \
                    and scored[1][3]["id"] != k["id"]:
                _, _, p2, k2 = scored[1]
                out["second"] = _alt(p2, k2)
                out["ambiguous"] = True
                out["alternatives"] = [_alt(pack, k), out["second"]]
            else:
                out["ambiguous"] = False
                out["alternatives"] = []
            return _demo_filter(out)

    # 3) 无 kp 命中 → 回退（调用方给推荐清单，不生成）
    return {"ok": False, "confidence": "low", "reason": "no_kp",
            "ambiguous": False, "alternatives": []}
