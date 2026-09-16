"""MathForge 两段式 skill 路由 v2（DECISIONS D025/D026）。

stage1 确定性索引：kp_id / subject_area / exam_id / 自由文本意图 → 候选包集合。
stage2 择一：候选=1 直接返回；>1 用 LLM 择一（走缓存）；
  MATHFORGE_DEMO=1 或无 key 时降级 path='stage2-fallback' 取候选首位，绝不崩；=0 返回 path='unrouted'。

route(ctx) -> {pack_id, examiner_prompt, confidence, path}
  ctx 可含：kp_id(str) | subject_area(str) | exam_id(str) | query/intent/text(str 自由文本)
"""

from __future__ import annotations

import os
import re

import pack_loader

# 模块级懒加载检索索引（各包 kp name/section/typical_forms/aliases 摊平）
_INDEX = None


def build_index() -> list[dict]:
    """把各包 kp 摊平成可检索表（内存，模块级懒加载）。"""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    entries = []
    for pid in pack_loader.list_packs():
        pack = pack_loader.load_pack(pid)
        for k in pack["kp_graph"]:
            forms = k.get("typical_forms", []) or []
            aliases = k.get("aliases", []) or []
            text = " ".join([str(k.get("name", "")), str(k.get("section", "")),
                             " ".join(map(str, forms)), " ".join(map(str, aliases))])
            entries.append({
                "pack_id": pid,
                "kp_id": k["id"],
                "name": k.get("name", ""),
                "section": k.get("section", ""),
                "forms": forms,
                "aliases": aliases,
                "text": text,
            })
    _INDEX = entries
    return _INDEX


def _match_entry(entry: dict, query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    if q in entry["text"]:
        return True
    # 候选 name/forms/aliases 整体出现在 query 中
    for token in [entry["name"], *entry["forms"], *entry["aliases"]]:
        if token and str(token) in q:
            return True
    # query 切词后任一词（≥2 字）是 entry 文本子串 —— 支持「矩阵的特征值」类自由文本
    for qt in re.split(r"[\s,，。、/与的]+", q):
        if len(qt) >= 2 and qt in entry["text"]:
            return True
    return False


def _free_text_candidates(query: str) -> list[str]:
    """自由文本意图 → 候选包 id 集合（去重保序）。"""
    cands = []
    for e in build_index():
        if _match_entry(e, query) and e["pack_id"] not in cands:
            cands.append(e["pack_id"])
    return cands


def _subject_candidates(subject_area: str) -> list[str]:
    cands = []
    for pid in pack_loader.list_packs():
        pack = pack_loader.load_pack(pid)
        name = (pack["manifest"].get("name") or "")
        desc = (pack["manifest"].get("description") or "")
        if subject_area in name or subject_area in desc:
            cands.append(pid)
    return cands


def _exam_candidates(exam_id: str) -> list[str]:
    try:
        exam = pack_loader.load_exam(exam_id)
    except Exception:  # noqa: BLE001
        return []
    packs = exam.get("packs") or exam.get("pack_combo") or []
    if isinstance(packs, dict):
        return list(packs.keys())
    return [p for p in packs if isinstance(p, str)]


def _demo_mode() -> bool:
    if os.environ.get("MATHFORGE_DEMO") == "1":
        return True
    if not os.environ.get("DEEPSEEK_API_KEY"):
        return True
    return False


def _llm_choose(query: str, candidates: list[str]) -> str:
    """stage2 LLM 择一；失败/DEMO 降级取首位。"""
    try:
        from llm import chat_json
        import json
        d = chat_json(
            [{"role": "system", "content": "从候选科目包中选最匹配学生意图的一个。"
                                           "只输出 JSON：{\"pack_id\": \"...\"}"},
             {"role": "user", "content": json.dumps({"intent": query, "candidates": candidates},
                                                    ensure_ascii=False)}],
            temperature=0.0, namespace="route:")
        pick = d.get("pack_id")
        if pick in candidates:
            return pick
    except Exception:  # noqa: BLE001 —— 任意异常都降级，绝不崩
        pass
    return candidates[0]


def route(ctx: dict) -> dict:
    """两段式路由。返回 {pack_id, examiner_prompt, confidence, path}。"""
    kp_id = ctx.get("kp_id")
    subject_area = ctx.get("subject_area")
    exam_id = ctx.get("exam_id")
    query = ctx.get("query") or ctx.get("intent") or ctx.get("text")

    candidates: list[str] = []
    stage1_kind = None

    if kp_id:
        pid = pack_loader.find_pack_for_kp(kp_id)
        candidates = [pid] if pid else []
        stage1_kind = "kp_id"
    elif exam_id:
        candidates = _exam_candidates(exam_id)
        stage1_kind = "exam_id"
    elif subject_area:
        candidates = _subject_candidates(subject_area)
        stage1_kind = "subject_area"
    elif query:
        candidates = _free_text_candidates(query)
        stage1_kind = "free_text"

    # stage2 择一
    if len(candidates) == 0:
        return {"pack_id": None, "examiner_prompt": None,
                "confidence": 0.0, "path": "unrouted"}
    if len(candidates) == 1:
        pid = candidates[0]
        return {"pack_id": pid,
                "examiner_prompt": pack_loader.load_pack(pid)["examiner_prompt"],
                "confidence": 1.0 if stage1_kind in ("kp_id", "exam_id", "subject_area") else 0.9,
                "path": "stage1"}
    # >1 候选 → LLM 择一
    if _demo_mode():
        pid = candidates[0]
        return {"pack_id": pid,
                "examiner_prompt": pack_loader.load_pack(pid)["examiner_prompt"],
                "confidence": 0.5, "path": "stage2-fallback"}
    pid = _llm_choose(query or "", candidates)
    return {"pack_id": pid,
            "examiner_prompt": pack_loader.load_pack(pid)["examiner_prompt"],
            "confidence": 0.8, "path": "stage2"}
