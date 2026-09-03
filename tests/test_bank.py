"""Day6 任务 1：题库直刷模块单测（bank.py）。

覆盖：加载归一化（含 kp 缺失兜底）/ 筛选维度 / 选择题判分 / 同类推荐。
不触网、不依赖真实 DB（记忆写入路径由 db.record_answer 既有测试口径保证）。
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bank as bank_mod  # noqa: E402


def _sample_raw():
    return [
        {"id": "t-1", "科目": "高等数学", "年份": 2024, "kp": "极限 / 极限 / 渐近线",
         "难度": None, "type": "single_choice", "statement_md": "$x$",
         "options": [{"id": "opt-a", "label": "A", "content_md": "1"},
                     {"id": "opt-b", "label": "B", "content_md": "2"}],
         "answer": "B", "analysis": "", "source_raw": "", "is_core": True},
        {"id": "t-2", "科目": "线性代数", "年份": None, "kp": None,
         "难度": None, "type": "subjective", "statement_md": "证明。",
         "options": None, "answer": "证明见解析", "analysis": "略", "source_raw": "", "is_core": False},
    ]


def test_load_bank_real_dataset():
    rows = bank_mod.load_bank()
    assert len(rows) == 128
    types = {r["qtype"] for r in rows}
    assert types == {"choice", "solution"}


def test_normalize_kp_null_fallback():
    q = bank_mod.normalize(_sample_raw()[1])
    assert q["kp"] == bank_mod.FALLBACK_KP
    assert q["kp_root"] == bank_mod.FALLBACK_KP
    assert q["qtype"] == "solution"
    assert q["year"] is None


def test_normalize_choice_mapping():
    q = bank_mod.normalize(_sample_raw()[0])
    assert q["qtype"] == "choice"
    assert q["kp_root"] == "极限"
    assert q["is_core"] is True


def test_facets_contains_dimensions():
    f = bank_mod.facets(bank_mod.load_bank())
    assert f["total"] == 128
    assert {d["v"] for d in f["qtypes"]} == {"choice", "solution"}
    assert any(d["v"] == "未标注" for d in f["years"])  # 年份缺失的题可见
    assert len(f["kp_roots"]) >= 10


def test_filter_by_subject_and_qtype():
    rows = bank_mod.filter_questions(bank_mod.load_bank(), subject="线性代数", qtype="choice")
    assert rows and all(r["subject"] == "线性代数" and r["qtype"] == "choice" for r in rows)


def test_filter_year_unlabeled():
    rows = bank_mod.filter_questions(bank_mod.load_bank(), year="未标注")
    assert rows and all(r["year"] is None for r in rows)


def test_filter_invalid_year_returns_none():
    # 对抗验收 major 修复回归：非法 year 不抛 ValueError，返回 None（路由层转 400）
    assert bank_mod.filter_questions(bank_mod.load_bank(), year="abcd") is None
    assert bank_mod.filter_questions(bank_mod.load_bank(), year="20;24") is None


def test_filter_negative_limit_returns_none():
    # 对抗验收 minor 修复回归：负 limit 不再返回全量
    assert bank_mod.filter_questions(bank_mod.load_bank(), limit=-5) is None
    assert bank_mod.filter_questions(bank_mod.load_bank(), offset=-1) is None


def test_filter_valid_year_still_works():
    rows = bank_mod.filter_questions(bank_mod.load_bank(), year="2025")
    assert rows and all(r["year"] == 2025 for r in rows)


def test_judge_choice():
    assert bank_mod.judge_choice("B", "B") is True
    assert bank_mod.judge_choice("b", "B") is True
    assert bank_mod.judge_choice("B. 任意选项文字", "B") is True
    assert bank_mod.judge_choice("A", "B") is False
    assert bank_mod.judge_choice("", "B") is False
    assert bank_mod.judge_choice("3", "B") is False


def test_similar_questions_prefers_same_kp():
    rows = bank_mod.load_bank()
    anchor = next(r for r in rows if sum(1 for x in rows if x["kp"] == r["kp"]) >= 2)
    sim = bank_mod.similar_questions(anchor["kp"], exclude_id=anchor["id"], bank=rows)
    root = anchor["kp"].split(" / ")[0]
    assert sim and all(q["id"] != anchor["id"] and q["kp_root"] == root for q in sim)
