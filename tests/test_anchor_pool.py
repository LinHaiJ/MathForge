# -*- coding: utf-8 -*-
"""模块 H（真题锚池扩容）单测。

覆盖：
- scripts/build_anchor_pool.py：清洗规则（题型过滤/空题干空答案/答案回退 correct_answer/
  content_hash 去重含空 hash 跳过/字段裁剪/原子落盘无 .tmp 残留）、kp 标注（词表包含匹配
  +最具体排序）、类目→科目映射、确定性（同名重跑逐字节一致）；
- generate.pick_few_shot 回退链（抗审后语义）：anchor_pool 存在 → 从池选（kp 命中优先）；
  池内 kp 零命中 → 并入 cxyonly 同 kp 候选（按 id 去重）；池缺失/解析为空 → 回退 cxyonly
  （现行为）；都没有 → 空；池文件截断行/垃圾行 → 跳过不炸正常出锚；
- 命名空间代次 bump 断言（green v6 / yellow v5，锚池扩容代次）。

密封性：输入全部在 tmp 目录构造——测试不依赖本机 cache/anchor_pool.jsonl 是否已生成
（monkeypatch 路径），零网络零真实 API（pick_few_shot 本身不调 LLM）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import generate  # noqa: E402

_SCRIPT = os.path.join(ROOT, "scripts", "build_anchor_pool.py")


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_anchor_pool", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


builder = _load_builder()


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def _write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# ---- 测试用最小数据 ----

def _raw_row(rid, stem, qtype="subjective", answer_ref="$e$", correct=None,
             analysis="解析文本", source="2025数一", chash=None, options=None,
             category_id=1, cat_full="高等数学 / 一元微分"):
    return {
        "id": rid, "stem": stem, "options": options or [],
        "answer": {"reference_answer_md": answer_ref},
        "answer_explanation": analysis, "source": source,
        "content_hash": chash or f"hash-{rid}", "question_type": qtype,
        "is_core": False, "category_id": category_id,
        "correct_answer": correct, "category_full_path": cat_full,
    }


def _cats():
    return {"1": "高等数学/一元微分", "2": "线性代数/矩阵", "3": "概率统计/事件与概率",
            "9": "历年真题/数一/2019"}


def _vocab(tmp_path):
    """构造单包 kp 词表：罗尔定理（含 typical_form）+ 行列式计算。"""
    packs = tmp_path / "packs" / "calculus"
    packs.mkdir(parents=True, exist_ok=True)
    _write_json(packs / "kp_graph.json", [
        {"id": "calc.rolle", "name": "罗尔定理", "typical_forms": ["f(a)=f(b) 求 ξ"]},
        {"id": "calc.det", "name": "行列式计算", "typical_forms": []},
    ])
    for pid in ("linear", "probability"):
        p = tmp_path / "packs" / pid
        p.mkdir(parents=True, exist_ok=True)
        _write_json(p / "kp_graph.json", [{"id": f"{pid}.x", "name": f"{pid}占位", "typical_forms": []}])
    return tmp_path / "packs"


# ---- H1 脚本清洗 ----

def test_clean_type_filter_answer_fallback_and_schema(tmp_path):
    raw = tmp_path / "raw.jsonl"
    rows = [
        _raw_row(1, "求 $f(x)=x^2$ 满足罗尔定理的 $\\xi$。"),                     # 有效主观题
        _raw_row(2, "下列说法正确的是", qtype="single_choice", answer_ref="",    # 单选：回退 correct
                 correct="C", options=[{"id": "opt-a", "label": "A", "content_md": "$x$"}]),
        _raw_row(3, "多选题", qtype="multiple_choice", answer_ref="", correct="AB"),  # 题型过滤
        _raw_row(4, ""),                                                          # 空题干
        _raw_row(5, "无答案题", answer_ref="", correct=None),                     # 空答案（无回退）
        _raw_row(6, "重复题干", chash="dup-hash"),                                # 与 7 同 hash
        _raw_row(7, "重复题干", chash="dup-hash"),
        _raw_row(8, "空哈希题甲", chash=""),                                      # 空 hash 跳过去重（minor-4）
        _raw_row(9, "空哈希题乙", chash=""),
    ]
    _write_jsonl(raw, rows)
    cats = tmp_path / "cats.json"
    _write_json(cats, _cats())
    out = tmp_path / "anchor_pool.jsonl"

    st = builder.build(raw, cats, _vocab(tmp_path), out)
    assert (st["raw_total"], st["dropped_type"], st["dropped_empty"], st["dedup_removed"]) == (9, 1, 2, 1)
    assert st["kept"] == 5

    entries = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert {e["id"] for e in entries} == {"cxy-1", "cxy-2", "cxy-6", "cxy-8", "cxy-9"}
    # 两道空 hash 的不同题都保留，未被并掉
    assert {"空哈希题甲", "空哈希题乙"} <= {e["statement_md"] for e in entries}
    by_id = {e["id"]: e for e in entries}
    # 单选答案回退 correct_answer；主观题用 reference_answer_md
    assert by_id["cxy-2"]["answer"] == "C" and by_id["cxy-2"]["type"] == "single_choice"
    assert by_id["cxy-1"]["answer"] == "$e$" and by_id["cxy-1"]["type"] == "subjective"
    # schema 与 cxyonly 对齐：pick_few_shot 必需键齐备；主观题 options 为 null
    for e in entries:
        for k in ("statement_md", "options", "answer", "analysis", "kp", "type"):
            assert k in e
    assert by_id["cxy-1"]["options"] is None
    assert by_id["cxy-2"]["options"] == [{"id": "opt-a", "label": "A", "content_md": "$x$"}]
    # 年份/来源解析
    assert by_id["cxy-1"]["年份"] == 2025 and by_id["cxy-1"]["source_raw"] == "2025数一"
    # 有解析与 kp 标注统计（5 条全有解析；仅题1命中"罗尔定理"）
    assert st["with_analysis"] == 5 and st["with_kp"] == 1


def test_kp_annotation_and_subject_mapping(tmp_path):
    raw = tmp_path / "raw.jsonl"
    rows = [
        _raw_row(1, "验证 $f(x)$ 在 $[a,b]$ 上满足罗尔定理。", cat_full="线性代数 / 矩阵"),  # kp 命中
        _raw_row(2, "求矩阵的逆。", category_id=2),                                       # 无 kp、线代类目
        _raw_row(3, "求极限。", category_id=1),                                           # 无 kp、高数类目
        _raw_row(4, "真题卷题目。", category_id=9),                                       # 历年真题类目 → 无科目
        _raw_row(5, "类目表缺失。", category_id=99, cat_full="概率统计 / 事件与概率"),      # 类目表缺失 → 回退记录路径
    ]
    _write_jsonl(raw, rows)
    cats = tmp_path / "cats.json"
    _write_json(cats, _cats())
    out = tmp_path / "anchor_pool.jsonl"

    st = builder.build(raw, cats, _vocab(tmp_path), out)
    entries = {json.loads(l)["id"]: json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()}
    # kp 命中：字段含 kp_graph 中文名
    assert "罗尔定理" in entries["cxy-1"]["kp"]
    # 未命中 kp：kp 为 None，subject 按类目映射
    assert entries["cxy-2"]["kp"] is None and entries["cxy-2"]["subject"] == "linear"
    assert entries["cxy-3"]["subject"] == "calculus"
    # 历年真题类目不映射科目
    assert entries["cxy-4"]["subject"] is None and entries["cxy-4"]["科目"] is None
    # 类目 id 缺失 → 回退记录内 category_full_path
    assert entries["cxy-5"]["subject"] == "probability"
    assert st["with_kp"] == 1
    assert st["subject_dist"] == {"calculus": 2, "linear": 1, "probability": 1}
    assert st["subject_none"] == 1  # 仅历年真题类目不可映射


def test_kp_most_specific_ordering(tmp_path):
    """多 kp 命中取最具体：最长命中 pattern 的 kp 排首位（可多值取最具体定案）。"""
    raw = tmp_path / "raw.jsonl"
    _write_jsonl(raw, [_raw_row(1, "用拉格朗日中值定理证明不等式证明。")])
    packs = tmp_path / "packs" / "calculus"
    packs.mkdir(parents=True)
    _write_json(packs / "kp_graph.json", [
        {"id": "calc.a", "name": "中值定理证明应用", "typical_forms": ["不等式证明"]},
        {"id": "calc.b", "name": "拉格朗日中值定理", "typical_forms": ["不等式证明"]},
    ])
    for pid in ("linear", "probability"):
        p = tmp_path / "packs" / pid
        p.mkdir(parents=True)
        _write_json(p / "kp_graph.json", [{"id": f"{pid}.x", "name": "占位", "typical_forms": []}])
    cats = tmp_path / "cats.json"
    _write_json(cats, _cats())
    out = tmp_path / "anchor_pool.jsonl"
    builder.build(raw, cats, tmp_path / "packs", out)
    entry = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    names = entry["kp"].split(" / ")
    assert names[0] == "拉格朗日中值定理"  # 命中 pattern 更长者最具体
    assert "中值定理证明应用" in names      # 其余真实命中者保留（多值）


def test_deterministic_byte_identical(tmp_path):
    raw = tmp_path / "raw.jsonl"
    rows = [_raw_row(i, f"题干 {i}：验证罗尔定理。", source=f"{2000+i}数一") for i in range(5)]
    rows += [_raw_row(6, "重复", chash="dup-hash"), _raw_row(7, "重复", chash="dup-hash")]
    _write_jsonl(raw, rows)
    cats = tmp_path / "cats.json"
    _write_json(cats, _cats())
    out1, out2 = tmp_path / "p1.jsonl", tmp_path / "p2.jsonl"
    builder.build(raw, cats, _vocab(tmp_path), out1)
    builder.build(raw, cats, _vocab(tmp_path), out2)
    assert out1.read_bytes() == out2.read_bytes()  # 同名重跑逐字节一致
    assert not list(tmp_path.glob("*.tmp"))        # 原子落盘（major-1）：无 .tmp 残留


# ---- H2 pick_few_shot 三级回退链 ----

def _pool_line(kp_name, qtype="subjective", statement="锚题干"):
    return json.dumps({"id": f"p-{kp_name}", "kp": kp_name, "type": qtype,
                       "statement_md": statement, "options": None, "answer": "$1$",
                       "analysis": "锚解析"}, ensure_ascii=False)


def test_pick_few_shot_prefers_anchor_pool_and_kp_hit(monkeypatch, tmp_path):
    pool = tmp_path / "anchor_pool.jsonl"
    # 行列式锚在前、罗尔锚在后：kp 命中评分(+3)必须反超池内顺序
    pool.write_text("\n".join([
        _pool_line("行列式计算", statement="行列式锚"),
        _pool_line("罗尔定理", statement="罗尔锚"),
    ]), encoding="utf-8")
    monkeypatch.setattr(generate, "ANCHOR_POOL", pool)
    monkeypatch.setattr(generate, "QUESTION_POOL", tmp_path / "missing_cxyonly.jsonl")

    out = generate.pick_few_shot("罗尔定理", "calculation", k=1)
    assert len(out) == 1 and "罗尔锚" in out[0]
    # 从池选：不含 cxyonly 独有内容
    out2 = generate.pick_few_shot("行列式计算", "solution", k=2)
    assert len(out2) == 2 and "行列式锚" in out2[0]


def test_pick_few_shot_falls_back_to_cxyonly(monkeypatch, tmp_path):
    only = tmp_path / "cxyonly.jsonl"
    only.write_text("\n".join([
        _pool_line("罗尔定理", qtype="single_choice", statement="单选锚"),
        _pool_line("中值定理", qtype="subjective", statement="主观锚"),
    ]), encoding="utf-8")
    monkeypatch.setattr(generate, "ANCHOR_POOL", tmp_path / "missing_pool.jsonl")
    monkeypatch.setattr(generate, "QUESTION_POOL", only)

    # 现行为（评分语义不变）：kp 命中 +3 反超同题型 +2 —— 单选锚 4 分 > 主观锚 3 分（分高者先）
    out = generate.pick_few_shot("罗尔定理", "calculation", k=2)
    assert "单选锚" in out[0] and "主观锚" in out[1]
    assert out[0].startswith("【真题锚 1】")


def test_pick_few_shot_empty_when_no_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(generate, "ANCHOR_POOL", tmp_path / "missing_pool.jsonl")
    monkeypatch.setattr(generate, "QUESTION_POOL", tmp_path / "missing_cxyonly.jsonl")
    assert generate.pick_few_shot("罗尔定理", "calculation") == []


def test_pick_few_shot_tolerates_corrupt_pool_lines(monkeypatch, tmp_path):
    """抗审 major-1：截断行/垃圾行/非 dict 行跳过不炸，可用行正常出锚。"""
    pool = tmp_path / "anchor_pool.jsonl"
    pool.write_text("\n".join([
        _pool_line("罗尔定理", statement="罗尔锚"),
        '{"id": "truncated", "kp": "罗尔定',   # 截断行（JSONDecodeError）
        "这不是JSON{{{",                        # 垃圾行
        "123",                                  # 合法 JSON 但非 dict
        "",                                     # 空行
    ]), encoding="utf-8")
    monkeypatch.setattr(generate, "ANCHOR_POOL", pool)
    monkeypatch.setattr(generate, "QUESTION_POOL", tmp_path / "missing_cxyonly.jsonl")
    out = generate.pick_few_shot("罗尔定理", "calculation", k=3)
    assert len(out) == 1 and "罗尔锚" in out[0]


def test_pick_few_shot_empty_or_garbage_pool_falls_back_to_cxyonly(monkeypatch, tmp_path):
    """抗审 major-1：锚池存在但解析为空（全垃圾行）→ 回退 cxyonly（现行为）。"""
    pool = tmp_path / "anchor_pool.jsonl"
    pool.write_text("垃圾行甲{{{\n垃圾行乙\n", encoding="utf-8")  # 存在但解析为空
    only = tmp_path / "cxyonly.jsonl"
    only.write_text(_pool_line("中值定理", statement="旧池锚"), encoding="utf-8")
    monkeypatch.setattr(generate, "ANCHOR_POOL", pool)
    monkeypatch.setattr(generate, "QUESTION_POOL", only)
    out = generate.pick_few_shot("中值定理", "calculation", k=1)
    assert len(out) == 1 and "旧池锚" in out[0]


def test_pick_few_shot_merges_cxyonly_when_pool_misses_kp(monkeypatch, tmp_path):
    """抗审 major-2：池内 kp 命中为零 → 并入 cxyonly 同 kp 候选，保住旧覆盖下限。"""
    pool = tmp_path / "anchor_pool.jsonl"
    pool.write_text(_pool_line("罗尔定理", statement="池锚"), encoding="utf-8")
    only = tmp_path / "cxyonly.jsonl"
    only.write_text("\n".join([
        _pool_line("行列式计算", statement="旧池行列式锚"),
        _pool_line("罗尔定理", statement="旧池罗尔锚"),
    ]), encoding="utf-8")
    monkeypatch.setattr(generate, "ANCHOR_POOL", pool)
    monkeypatch.setattr(generate, "QUESTION_POOL", only)
    # 「行列式计算」只在 cxyonly 有题：池内零命中 → 并入后 kp+3 排首
    out = generate.pick_few_shot("行列式计算", "calculation", k=2)
    assert len(out) == 2 and "旧池行列式锚" in out[0] and "池锚" in out[1]
    # 池内 kp 命中时绝不并入：旧池同 kp 锚不参与
    out2 = generate.pick_few_shot("罗尔定理", "calculation", k=2)
    assert len(out2) == 1 and "池锚" in out2[0]


def test_pick_few_shot_merge_dedupes_by_id(monkeypatch, tmp_path):
    """抗审 major-2：合并按 id 去重；同 id 且池内副本无 kp 标注时用 cxyonly 精标副本顶替
    （cxyonly 的 id 全部 ⊆ 锚池，若纯跳过去重会把保底整体架空）。"""
    def entry(eid, kp_name, stmt):
        return json.dumps({"id": eid, "kp": kp_name, "type": "subjective",
                           "statement_md": stmt, "options": None, "answer": "$1$",
                           "analysis": "解析"}, ensure_ascii=False)
    pool = tmp_path / "anchor_pool.jsonl"
    pool.write_text(entry("cxy-1", "罗尔定理", "池内同id不命中"), encoding="utf-8")
    only = tmp_path / "cxyonly.jsonl"
    only.write_text("\n".join([
        entry("cxy-1", "行列式计算", "同id旧池命中"),   # id 与池内重复，池内副本无 kp 标注
        entry("cxy-2", "行列式计算", "旧池id2"),
    ]), encoding="utf-8")
    monkeypatch.setattr(generate, "ANCHOR_POOL", pool)
    monkeypatch.setattr(generate, "QUESTION_POOL", only)
    out = generate.pick_few_shot("行列式计算", "calculation", k=5)
    stmts = "\n".join(out)
    assert stmts.count("【真题锚") == 2        # id 在合并列表中唯一（去重）
    assert "同id旧池命中" in stmts             # cxyonly 精标副本顶替池内同 id 槽位
    assert "池内同id不命中" not in stmts       # 无标注副本不再参与
    assert "旧池id2" in stmts                  # 非重复的同 kp 候选正常并入


# ---- H2 命名空间代次 bump ----

def test_namespace_bumped_for_anchor_pool_generation():
    """green v9（模块 M 占位符契约换代+系数位契约对齐）/ yellow v6（模块 J 伪考点闸门代次）；ablation 臂同步 v2。"""
    spec = importlib.util.spec_from_file_location("generate_fresh", os.path.join(ROOT, "generate.py"))
    fresh = importlib.util.module_from_spec(spec)
    ablation = os.environ.get("MATHFORGE_ABLATION")
    if ablation:
        del os.environ["MATHFORGE_ABLATION"]  # fresh 实例按默认（非 ablation）分支断言
    try:
        spec.loader.exec_module(fresh)
    finally:
        if ablation:
            os.environ["MATHFORGE_ABLATION"] = ablation
    assert fresh._NS_GREEN == "gen-green:v9:a{}:"
    assert fresh._NS_YELLOW == "gen-yellow:v6:a{}:"
    assert generate._NS_GREEN.startswith("gen-green-ab:v2") if generate._ABLATION \
        else generate._NS_GREEN == "gen-green:v9:a{}:"
