"""题库直刷（Day6 任务 1）：cxyonly.jsonl 128 题加载 / 筛选 / 判分辅助。

- 数据不出本地（只读本地题集文件，不上传/外发）
- 难度字段 128/128 全为 null → 筛选维度只提供 科目×年份×题型×知识点（不设难度筛选器）
- 题型映射：single_choice → choice（选项匹配本地判分）；subjective → solution（任务 2 自评，不做机器判分）
- 刷题产生的 kp 归因走既有 db.record_answer 写入 mastery/mistakes——与练习页共用同一记忆系统
"""

from __future__ import annotations

import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

BANK_PATH = Path(__file__).resolve().parent / "题集" / "cxyonly.jsonl"
FALLBACK_KP = "未分类"  # 题集中 3 题 kp 为 null（cxy-5269/8325/8326），归入「未分类」保证可刷可入记忆

QTYPES = {"choice": "选择题", "solution": "解答题"}


@lru_cache(maxsize=1)
def load_bank(path: str | None = None) -> list[dict]:
    """加载并归一化题库（进程内缓存；仅供本地服务使用）。"""
    p = Path(path) if path else BANK_PATH
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(normalize(json.loads(line)))
    return out


def normalize(raw: dict) -> dict:
    kp = raw.get("kp") or FALLBACK_KP
    return {
        "id": raw["id"],
        "kp": kp,
        "kp_root": kp.split(" / ")[0],
        "subject": raw.get("科目") or "未标注",
        "year": raw.get("年份"),  # 可能为 None → 展示为「未标注」
        "qtype": "solution" if raw.get("type") == "subjective" else "choice",
        "statement_md": raw.get("statement_md") or "",
        "options": raw.get("options") or None,  # list[{id,label,content_md}] 或 None
        "answer": raw.get("answer") or "",
        "analysis": raw.get("analysis") or "",
        "source_raw": raw.get("source_raw") or "",
        "is_core": bool(raw.get("is_core")),
    }


def facets(bank: list[dict] | None = None) -> dict:
    """筛选器可用维度：科目 / 年份 / 题型 / 知识板块（一级）/ 知识点（完整）。"""
    bank = bank if bank is not None else load_bank()
    years = sorted({q["year"] for q in bank if q["year"] is not None}, reverse=True)
    return {
        "total": len(bank),
        "subjects": [{"v": s, "n": n} for s, n in Counter(q["subject"] for q in bank).most_common()],
        "years": [{"v": str(y), "n": sum(1 for q in bank if q["year"] == y)} for y in years]
                 + [{"v": "未标注", "n": sum(1 for q in bank if q["year"] is None)}],
        "qtypes": [{"v": k, "label": v, "n": sum(1 for q in bank if q["qtype"] == k)}
                   for k, v in QTYPES.items()],
        "kp_roots": [{"v": r, "n": n} for r, n in
                     Counter(q["kp_root"] for q in bank).most_common()],
        "kps": [{"v": k, "root": k.split(" / ")[0], "n": n} for k, n in
                Counter(q["kp"] for q in bank).most_common()],
    }


def filter_questions(bank: list[dict] | None = None, *, subject: str | None = None,
                     year: str | None = None, qtype: str | None = None,
                     kp: str | None = None, kp_root: str | None = None,
                     limit: int = 200, offset: int = 0) -> list[dict] | None:
    """多条件筛选（全部为可选；year 传「未标注」匹配年份缺失的题）。

    year/limit/offset 非法时返回 None（由路由层转 400），不抛异常。
    """
    bank = bank if bank is not None else load_bank()
    if year and year != "未标注" and not re.fullmatch(r"\d{4}", year):
        return None
    if limit < 0 or offset < 0:
        return None
    out = []
    for q in bank:
        if subject and q["subject"] != subject:
            continue
        if year and q["year"] != (None if year == "未标注" else int(year)):
            continue
        if qtype and q["qtype"] != qtype:
            continue
        if kp and q["kp"] != kp:
            continue
        if kp_root and q["kp_root"] != kp_root:
            continue
        out.append(q)
    return out[offset:offset + limit]


def judge_choice(student: str, answer: str) -> bool:
    """选择题判分：提取选项字母（A-D）比较，大小写/多余文字不敏感。"""
    def letter(s: str) -> str:
        m = re.search(r"[A-Da-d]", str(s))
        return m.group(0).upper() if m else ""
    ls, la = letter(student), letter(answer)
    return bool(ls) and ls == la


def similar_questions(kp: str, exclude_id: str | None = None, limit: int = 3,
                      bank: list[dict] | None = None) -> list[dict]:
    """推荐同类：同 kp 优先，不足补同知识板块（kp_root），排除刚做过的题。"""
    bank = bank if bank is not None else load_bank()
    same_kp = [q for q in bank if q["kp"] == kp and q["id"] != exclude_id]
    same_root = [q for q in bank
                 if q["kp_root"] == kp.split(" / ")[0] and q["kp"] != kp and q["id"] != exclude_id]
    return (same_kp + same_root)[:limit]
