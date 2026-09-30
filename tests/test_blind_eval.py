# -*- coding: utf-8 -*-
"""模块 K（盲评工具）测试：组装逻辑 / 盲态分离 / 闸门 / 统计。

红线对齐（tests 全程零真实出网）：
- conftest 密封环境：MATHFORGE_DEMO=1、无 key、LLM 缓存指向临时目录；
- 需要在线态的用例显式 monkeypatch 假 key（MATHFORGE_DEMO=0 + DEEPSEEK_API_KEY=offline-test）；
- **LLM 炸弹**：llm.chat / llm.chat_json / generate.chat_json 一律替换为遇调即炸的桩——
  dry-run 零 API 断言的判据就是「炸弹不被触发」；
- build 用例的 AI 变式由假 generate_question 提供；对照的包内族走真实确定性枚举
  （SymPy 构造，零 LLM 零网络），不 mock 族路径——族轮转/守卫过滤一并被真实覆盖。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "build_blind_eval.py"

RUBRIC_DIMS = ["题干表述规范", "条件自洽", "难度与考点匹配", "解析质量", "命题专业度"]


def _load_module():
    """按路径加载 scripts/build_blind_eval.py（scripts 非包，tests 直接 import 不解析）。"""
    spec = importlib.util.spec_from_file_location("build_blind_eval", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_blind_eval", mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bf():
    return _load_module()


@pytest.fixture()
def online(monkeypatch):
    """假在线态：闸门放行，但 LLM 全部换炸弹（任何真实调用即炸=测试失败）。"""
    import generate
    import llm

    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key-not-real")

    def _bomb(*a, **kw):  # pragma: no cover —— 触发即测试失败
        raise AssertionError("LLM BOMB：测试中出现真实 LLM 调用（零出网红线被破坏）")

    monkeypatch.setattr(llm, "chat", _bomb)
    monkeypatch.setattr(llm, "chat_json", _bomb)
    monkeypatch.setattr(generate, "chat_json", _bomb)


@pytest.fixture()
def fake_pool(tmp_path):
    """假锚池：罗尔定理 8 道、参数方程求导 3 道（主观题+解析；一道带字典选项）。"""
    rows = []
    for i in range(8):
        rows.append({
            "id": f"cxy-test-{i}", "kp": "罗尔定理", "type": "subjective",
            "statement_md": f"真题(罗尔)第{i}题：设 $f(x)=x^{2}+{i}$ 验证罗尔定理并求 ξ。",
            "options": None, "answer": f"\\xi={i}", "analysis": f"真题解析{i}", "content_hash": f"h{i}",
        })
    for i in range(3):
        rows.append({
            "id": f"cxy-param-{i}", "kp": "参数方程求导", "type": "subjective",
            "statement_md": f"真题(参数方程)第{i}题：求 dy/dx。",
            "options": None, "answer": f"-\\frac{{t}}{{2}}\\cdot{i}",
            "analysis": f"参数方程解析{i}", "content_hash": f"p{i}",
        })
    rows[0]["options"] = [{"label": "A", "content_md": "甲"}, {"label": "B", "content_md": "乙"}]
    p = tmp_path / "anchor_pool.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return p


@pytest.fixture()
def fake_ai(monkeypatch):
    """假 AI 变式发生器：绿标形态、每次题干不同（计数器）。返回调用计数器。"""
    import generate

    calls = {"n": 0}

    def fake_generate_question(kp_context, difficulty="基础", qtype="calculation",
                               variant_of=None, **kw):
        calls["n"] += 1
        return {
            "kp": kp_context.get("kp", ""), "difficulty": difficulty, "qtype": qtype,
            "verify_level": "green",
            "statement_md": f"(AI#{calls['n']}) 假AI变式题干，考点 {kp_context.get('kp')}，母本「{(variant_of or '')[:12]}」。",
            "answer_sympy": "Rational(1,2)", "answer_md": None, "options": None,
            "analysis": "假AI解析：对母本做参数扰动。", "params": {"a": 1},
            "verification": {"solver_agree": True},
        }

    monkeypatch.setattr(generate, "generate_question", fake_generate_question)
    return calls


def _build(bf, tmp_path, fake_pool, args_extra):
    out = tmp_path / "blind_out"
    rc = bf.main(["--kps", "罗尔定理,参数方程求导", "--pairs", "5",
                  "--out", str(out), "--anchor-pool", str(fake_pool)] + args_extra)
    return rc, out


# ---------------------------------------------------------------- 干预与统计 ----
def test_wilson_known_values(bf):
    assert bf.wilson(0, 0) is None
    assert bf.wilson(3, 0) is None
    lo, hi = bf.wilson(5, 10)
    assert abs(lo - 0.2366) < 1e-3 and abs(hi - 0.7634) < 1e-3   # 5/10 已知区间
    lo, hi = bf.wilson(1, 1)
    assert abs(lo - 0.2065) < 1e-3 and hi == 1.0                  # 1/1 上界收缩到 1
    lo, hi = bf.wilson(0, 10)
    assert lo == 0.0 and abs(hi - 0.2775) < 1e-3                  # 0/10 下界 0
    lo, hi = bf.wilson(30, 30)
    assert abs(lo - 0.8865) < 1e-3 and hi == 1.0
    lo, hi = bf.wilson(2, 3)
    assert abs(lo - 0.2077) < 1e-3 and abs(hi - 0.9385) < 1e-3


def test_rubric_dims_pinned(bf):
    assert bf.RUBRIC_DIMS == RUBRIC_DIMS


# ---------------------------------------------------------------- 闸门 ----
def test_demo_mode_refuses_startup(bf, capsys):
    """demo 态（conftest 密封默认）直接退出，exit 2，不静默降级。"""
    with pytest.raises(SystemExit) as ei:
        bf.main(["--kps", "罗尔定理", "--pairs", "1"])
    assert ei.value.code == 2
    assert "demo" in capsys.readouterr().err


def test_missing_key_refuses_startup(bf, monkeypatch, capsys):
    """非 demo 但无 key 同样拒绝（在线态闸门的另一半）。"""
    monkeypatch.delenv("MATHFORGE_DEMO", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(SystemExit) as ei:
        bf.main(["--kps", "罗尔定理", "--pairs", "1"])
    assert ei.value.code == 2
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err


def test_unknown_kp_rejected(bf, online, tmp_path, fake_pool):
    with pytest.raises(SystemExit) as ei:
        bf.main(["--kps", "不存在的考点", "--pairs", "1", "--out", str(tmp_path),
                 "--anchor-pool", str(fake_pool)])
    assert ei.value.code == 2


# ---------------------------------------------------------------- dry-run ----
def test_dry_run_zero_api(bf, online, tmp_path, fake_pool, capsys):
    """dry-run：LLM 炸弹不触发 + 不产生任何 session 目录。"""
    rc, out = _build(bf, tmp_path, fake_pool, [])
    assert rc == 0
    txt = capsys.readouterr().out
    assert "dry-run" in txt and "预计 LLM 调用" in txt and "未调任何 API" in txt
    assert not out.exists() or not list(out.glob("session_*"))


def test_dry_run_reports_control_kinds(bf, online, tmp_path, fake_pool, capsys):
    _build(bf, tmp_path, fake_pool, [])
    out_txt = capsys.readouterr().out
    assert "家族换数" in out_txt          # 罗尔定理 → 包内 rolle_roots 族（真实枚举）
    assert "第二道AI" in out_txt          # 参数方程求导 → 无族，如实降级标注


def test_plan_capacity_subjective_only(bf, online, tmp_path):
    """容量口径只计主观题：选择题真题不与填空型变式配对（光凭题型即识破 AI）。"""
    import argparse

    pool = tmp_path / "pool_choice.jsonl"
    rows = [{"id": f"c{i}", "kp": "罗尔定理", "type": "single_choice",
             "statement_md": f"选择题{i}", "options": [{"label": "A", "content_md": "1"}],
             "answer": "A", "analysis": "解析"} for i in range(5)]
    rows += [{"id": f"s{i}", "kp": "罗尔定理", "type": "subjective",
              "statement_md": f"主观题{i}", "options": None,
              "answer": "1", "analysis": "解析"} for i in range(2)]
    pool.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    plan = bf.build_plan(argparse.Namespace(
        kps="罗尔定理", all=False, pairs=4, difficulty="基础", out=str(tmp_path / "o"),
        seed=None, build=False, score=None, anchor_pool=str(pool)))
    e = plan["kps"][0]
    assert e["anchor_count"] == 7 and e["ori_available"] == 2
    assert e["capacity"] == 2 and e["pairs_planned"] == 2   # 要 4 对，主观题只够 2 对


# ---------------------------------------------------------------- build ----
def test_build_structure_and_blind_key_separation(bf, online, tmp_path, fake_pool, fake_ai):
    rc, out = _build(bf, tmp_path, fake_pool, ["--build", "--seed", "7"])
    assert rc == 0
    sessions = sorted(out.glob("session_*"))
    assert len(sessions) == 1
    sdir = sessions[0]

    blind = json.loads((sdir / "blind.json").read_text(encoding="utf-8"))
    key = json.loads((sdir / "key.json").read_text(encoding="utf-8"))
    manifest = json.loads((sdir / "manifest.json").read_text(encoding="utf-8"))

    assert len(blind["pairs"]) == 5 and len(key["pairs"]) == 5
    assert blind["rubric_dims"] == RUBRIC_DIMS
    assert (sdir / "index.html").read_text(encoding="utf-8") == \
        (_ROOT / "blind_eval" / "index.html").read_text(encoding="utf-8")

    # 盲态保证：blind.json 条目只允许题面字段，绝无 provenance/答案/解析/左右归属
    for p in blind["pairs"]:
        assert set(p.keys()) == {"pair_id", "kp", "ori", "left", "right"}
        for side in ("ori", "left", "right"):
            assert set(p[side].keys()) <= {"item_id", "statement_md", "options"}
            assert p[side]["statement_md"].strip()
    blob = json.dumps(blind, ensure_ascii=False)
    for forbidden in ('"provenance"', '"answer"', '"analysis"', '"ai_side"',
                      '"source_desc"', '"verify_level"', '"family"'):
        assert forbidden not in blob, f"blind.json 泄漏字段：{forbidden}"

    # key 侧：ai_side 与 provenance 一致；每对恰有 ai_side 记录，blind 中无
    for kp_key, p in zip(key["pairs"], blind["pairs"]):
        assert kp_key["pair_id"] == p["pair_id"]
        assert kp_key["ai_side"] in ("left", "right")
        assert set(kp_key.keys()) >= {"pair_id", "kp", "ai_side", "control_kind",
                                      "left", "right", "ori"}
        for side in ("left", "right"):
            it = kp_key[side]
            assert it["provenance"] in ("AI", "family")
            assert it["answer"] and it["analysis"]
        assert kp_key[kp_key["ai_side"]]["provenance"] == "AI"
        other = "right" if kp_key["ai_side"] == "left" else "left"
        want = "family" if kp_key["control_kind"].startswith("family") else "AI"
        assert kp_key[other]["provenance"] == want
        assert kp_key["ori"]["provenance"] == "ori"
    assert '"ai_side"' not in json.dumps(blind, ensure_ascii=False)

    # 对照类型定案：罗尔定理=包内族（真实 rolle_roots 枚举），参数方程求导=第二道 AI（如实标注）
    kinds = {e["kp"]: e["control_kind"] for e in manifest["kps"]}
    assert kinds["罗尔定理"] == "family_pack"
    assert kinds["参数方程求导"] == "ai_second"
    assert manifest["built_pairs"] == 5
    assert manifest["seed"] == 7
    # m-10：plan 私有键（族实例题面/答案）剥离，manifest 无题面泄漏
    manifest_blob = (sdir / "manifest.json").read_text(encoding="utf-8")
    assert all(not k.startswith("_") for e in manifest["kps"] for k in e)
    assert '"statement_md"' not in manifest_blob and '"_pack_insts"' not in manifest_blob
    second_pairs = [k for k in key["pairs"] if k["control_kind"] == "ai_second"]
    assert second_pairs and all(
        k["left"]["provenance"] == "AI" and k["right"]["provenance"] == "AI"
        for k in second_pairs)

    # 锚池选项字典归一为字符串（真题第 0 题带 {label, content_md} 选项）
    ori0 = next(p["ori"] for p in blind["pairs"] if p["pair_id"] == "p001")
    assert ori0["options"] in (None, ["A. 甲", "B. 乙"]) or all(
        isinstance(o, str) for o in (ori0["options"] or []))


def test_build_left_right_random_and_idempotent(bf, online, tmp_path, fake_pool, fake_ai):
    """左右随机：多 seed 下两侧都出现；每次 build 新 session 目录（不覆盖）。"""
    seen_sides = set()
    for seed in range(6):
        rc, out = _build(bf, tmp_path, fake_pool, ["--build", "--seed", str(seed)])
        assert rc == 0
        sdirs = sorted(out.glob("session_*"))
        assert len(sdirs) == seed + 1          # 幂等：只增不覆盖
        key = json.loads((sdirs[-1] / "key.json").read_text(encoding="utf-8"))
        for k in key["pairs"]:
            if k["control_kind"].startswith("family"):
                # 族对照：AI 恰在一侧，且与 ai_side 记录一致
                assert (k["left"]["provenance"] == "AI") == (k["ai_side"] == "left")
                assert (k["right"]["provenance"] == "AI") == (k["ai_side"] == "right")
            else:
                # 无族对照：两道都是 AI 变式（如实标注），ai_side 仍指认「母本意义上的 AI 变式」
                assert k["left"]["provenance"] == "AI" and k["right"]["provenance"] == "AI"
            seen_sides.add(k["ai_side"])
    assert seen_sides == {"left", "right"}     # 6 seeds × 5 对必双侧出现（固定 seed → 确定性）


def test_build_blocked_ai_variant_skips_pair(bf, online, tmp_path, fake_pool, monkeypatch, capsys):
    """AI 变式全被拦截 → 全跳过不落空 session（目录被清理 + 人话提示，m-5）。"""
    import generate

    def blocked(*a, **kw):
        return {"kp": (a[0] or {}).get("kp", ""), "status": "blocked_pending_human",
                "statement_md": None, "error": "盲解与构造答案不一致（假）"}

    monkeypatch.setattr(generate, "generate_question", blocked)
    rc, out = _build(bf, tmp_path, fake_pool, ["--build", "--seed", "1"])
    assert rc == 0
    assert not list(out.glob("session_*")), "全跳过时不得留下空 session 目录"
    err = capsys.readouterr().err
    assert "0 对可评" in err and "未落任何产物" in err
    assert "常见原因" in err           # 跳过原因与人话指引都给出


def test_build_session_cross_file_consistency(bf, online, tmp_path, fake_pool, fake_ai):
    """页面冒烟前提：session 内 blind/key 的 item_id 逐对对齐（3 对假 session）。"""
    out = tmp_path / "blind_out"
    rc = bf.main(["--kps", "罗尔定理", "--pairs", "3", "--out", str(out),
                  "--anchor-pool", str(fake_pool), "--build", "--seed", "3"])
    assert rc == 0
    sdir = sorted(out.glob("session_*"))[-1]
    blind = json.loads((sdir / "blind.json").read_text(encoding="utf-8"))
    key = json.loads((sdir / "key.json").read_text(encoding="utf-8"))
    assert len(blind["pairs"]) == 3
    by_id = {k["pair_id"]: k for k in key["pairs"]}
    for p in blind["pairs"]:
        k = by_id[p["pair_id"]]
        assert p["left"]["item_id"] == k["left"]["item_id"]
        assert p["right"]["item_id"] == k["right"]["item_id"]
        assert p["ori"]["item_id"] == k["ori"]["item_id"]
        assert p["left"]["item_id"].endswith("-L") and p["right"]["item_id"].endswith("-R")


def test_build_ai_same_as_ori_skipped(bf, online, tmp_path, fake_pool, monkeypatch, capsys):
    """m-4：AI 变式与母本题干相同 → 跳过并记录原因（防自暴露对流出）。"""
    import generate

    # 注意：fixture 题干是 f-string，`x^{2}` 会被渲染成 `x^2`——此处必须用渲染后形态
    ORI = "真题(罗尔)第0题：设 $f(x)=x^2+0$ 验证罗尔定理并求 ξ。"

    def same(*a, **kw):
        return {"kp": (a[0] or {}).get("kp", ""), "verify_level": "green",
                "statement_md": ORI, "answer_sympy": "1", "analysis": "解析"}

    monkeypatch.setattr(generate, "generate_question", same)
    rc, out = _build(bf, tmp_path, fake_pool, ["--build", "--seed", "2"])
    assert rc == 0
    assert "与母本撞题" in capsys.readouterr().out
    # 罗尔仅第 1 对撞母本（其余照常成对）；manifest 必记录撞题原因
    sdir = sorted(out.glob("session_*"))[-1]
    manifest = json.loads((sdir / "manifest.json").read_text(encoding="utf-8"))
    assert any("母本题干相同" in s["reason"] for s in manifest["skipped"])


# ---------------------------------------------------------------- 统计聚合 ----
def _fake_key_and_results():
    key_by_pair = {
        "p1": {"pair_id": "p1", "kp": "罗尔定理", "ai_side": "left",
               "left": {"item_id": "p1-L"}, "right": {"item_id": "p1-R"}},
        "p2": {"pair_id": "p2", "kp": "罗尔定理", "ai_side": "right",
               "left": {"item_id": "p2-L"}, "right": {"item_id": "p2-R"}},
        "p3": {"pair_id": "p3", "kp": "二重积分", "ai_side": "left",
               "left": {"item_id": "p3-L"}, "right": {"item_id": "p3-R"}},
        "p4": {"pair_id": "p4", "kp": "二重积分", "ai_side": "right",
               "left": {"item_id": "p4-L"}, "right": {"item_id": "p4-R"}},
    }
    full5 = {d: 5 for d in RUBRIC_DIMS}
    full3 = {d: 3 for d in RUBRIC_DIMS}
    full4 = {d: 4 for d in RUBRIC_DIMS}
    results = [
        {"pair_id": "p1", "choice": "left", "rubric": {"p1-L": full5}, "note": "题干啰嗦"},
        {"pair_id": "p2", "choice": "left", "rubric": {"p2-R": full4, "p2-L": full3}, "note": ""},
        {"pair_id": "p3", "choice": "unsure", "rubric": {"p3-L": full3, "p3-R": full3}, "note": ""},
        {"pair_id": "p4", "choice": "right", "rubric": {"p4-R": full3}, "note": "包装痕迹重"},
    ]
    return key_by_pair, results


def test_aggregate_results_detection_wilson_rubric(bf):
    key_by_pair, results = _fake_key_and_results()
    s = bf.aggregate_results(results, key_by_pair, {})
    assert s["decisive"] == 3 and s["correct"] == 2 and s["unsure"] == 1
    assert abs(s["detection_rate"] - 2 / 3) < 1e-4
    lo, hi = s["wilson95"]
    assert abs(lo - 0.2077) < 1e-3 and abs(hi - 0.9385) < 1e-3   # wilson(2,3) 已知区间
    assert s["baseline"] == 0.5
    assert s["pairs_scored"] == s["decisive"] + s["unsure"] == 4   # m-3 口径
    assert s["results_not_in_key"] == 0
    # 顶层=all 桶（兼容字段），buckets 三档齐全
    assert s["buckets"]["all"]["detection_rate"] == s["detection_rate"]
    assert set(s["buckets"].keys()) == {"all", "family", "ai_second"}
    # 分组按「题」不按「对」：p1-L/p4-R（被指认为 AI）judged_ai；p2-L（被指认为原题，
    # 实为误指）judged_ai 同组；p2-R（被指认为原题那道）judged_family；p3 双题 unsure。
    assert abs(s["rubric_means"]["judged_ai"]["题干表述规范"] - 11 / 3) < 1e-3  # 脚本侧保留 3 位小数
    assert s["rubric_counts"]["judged_ai"]["题干表述规范"] == 3
    assert s["rubric_means"]["judged_family"]["题干表述规范"] == 4.0    # p2-R=4
    assert s["rubric_counts"]["judged_family"]["题干表述规范"] == 1
    assert s["rubric_means"]["unsure"]["解析质量"] == 3.0
    assert s["rubric_counts"]["unsure"]["解析质量"] == 2
    # 破绽清单 = 被识破（指认正确）的 AI 题
    assert [f["pair_id"] for f in s["flaws"]] == ["p1", "p4"]
    assert s["flaws"][0]["note"] == "题干啰嗦"


def test_aggregate_results_out_of_range_scores_ignored(bf):
    key_by_pair, results = _fake_key_and_results()
    results[0]["rubric"]["p1-L"]["题干表述规范"] = 9      # 越界分忽略
    results[1]["choice"] = None                            # 未作答对不计
    s = bf.aggregate_results(results, key_by_pair, {})
    assert s["decisive"] == 2 and s["correct"] == 2        # p2 未作答；p1/p4 仍决断且正确
    # m-3：未作答对不计入 pairs_scored（决断 2 + 分不出 1）
    assert s["pairs_scored"] == 3
    # p1 的 9 分被忽略后，judged_ai 组该维只剩 p4-R=3
    assert s["rubric_counts"]["judged_ai"]["题干表述规范"] == 1
    assert s["rubric_means"]["judged_ai"]["题干表述规范"] == 3.0


def test_aggregate_results_control_kind_buckets(bf):
    """M-2c：ai_second 对不混入 family 主口径——三桶数值互不相同且各自正确。

    编排：p1 family·AI左·判对；p2 family·AI右·误判；p3 ai_second·判对；
    p4 ai_second·判对；p5 family·分不出。
      family 桶   : 决断 2 对 1 → 50%   wilson(1,2)
      ai_second 桶: 决断 2 对 2 → 100%  wilson(2,2)
      all 桶      : 决断 4 对 3 → 75%   wilson(3,4)
    """
    key_by_pair = {
        "p1": {"pair_id": "p1", "kp": "罗尔定理", "ai_side": "left", "control_kind": "family_pack",
               "left": {"item_id": "p1-L"}, "right": {"item_id": "p1-R"}},
        "p2": {"pair_id": "p2", "kp": "罗尔定理", "ai_side": "right", "control_kind": "family_root",
               "left": {"item_id": "p2-L"}, "right": {"item_id": "p2-R"}},
        "p3": {"pair_id": "p3", "kp": "参数方程求导", "ai_side": "left", "control_kind": "ai_second",
               "left": {"item_id": "p3-L"}, "right": {"item_id": "p3-R"}},
        "p4": {"pair_id": "p4", "kp": "参数方程求导", "ai_side": "right", "control_kind": "ai_second",
               "left": {"item_id": "p4-L"}, "right": {"item_id": "p4-R"}},
        "p5": {"pair_id": "p5", "kp": "罗尔定理", "ai_side": "left", "control_kind": "family_pack",
               "left": {"item_id": "p5-L"}, "right": {"item_id": "p5-R"}},
    }
    results = [
        {"pair_id": "p1", "choice": "left", "rubric": {}, "note": ""},
        {"pair_id": "p2", "choice": "left", "rubric": {}, "note": ""},
        {"pair_id": "p3", "choice": "left", "rubric": {}, "note": ""},
        {"pair_id": "p4", "choice": "right", "rubric": {}, "note": ""},
        {"pair_id": "p5", "choice": "unsure", "rubric": {}, "note": ""},
    ]
    s = bf.aggregate_results(results, key_by_pair, {})
    b = s["buckets"]
    assert (b["family"]["decisive"], b["family"]["correct"]) == (2, 1)
    assert b["family"]["detection_rate"] == 0.5
    assert b["family"]["wilson95"] == [round(v, 4) for v in bf.wilson(1, 2)]
    assert b["family"]["unsure"] == 1                                  # p5 留在 family 桶
    assert (b["ai_second"]["decisive"], b["ai_second"]["correct"]) == (2, 2)
    assert b["ai_second"]["detection_rate"] == 1.0
    assert b["ai_second"]["wilson95"] == [round(v, 4) for v in bf.wilson(2, 2)]
    assert (b["all"]["decisive"], b["all"]["correct"]) == (4, 3)
    assert abs(b["all"]["detection_rate"] - 0.75) < 1e-9
    assert b["all"]["wilson95"] == [round(v, 4) for v in bf.wilson(3, 4)]
    # 三桶互不相同（分桶确实改变了口径，而不是复制混桶）
    assert len({b["family"]["detection_rate"], b["ai_second"]["detection_rate"],
                b["all"]["detection_rate"]}) == 3
    assert s["detection_rate"] == b["all"]["detection_rate"]
    assert s["pairs_scored"] == 5                                      # 4 决断 + 1 分不出


def _write_session(tmp_path, key, results, session="session_test", blind_pairs=None):
    sdir = tmp_path / session
    sdir.mkdir(exist_ok=True)
    (sdir / "key.json").write_text(json.dumps(key, ensure_ascii=False), encoding="utf-8")
    (sdir / "blind.json").write_text(json.dumps({"session": session, "pairs": blind_pairs or []},
                                                ensure_ascii=False), encoding="utf-8")
    rp = sdir / "results.json"
    rp.write_text(json.dumps({"session": session, "results": results},
                             ensure_ascii=False), encoding="utf-8")
    return rp


def test_score_offline_bypasses_online_gate(bf, tmp_path, capsys):
    """--score 纯本地复算：demo 密封态（无 key）照常可用，零 API。"""
    key_by_pair, results = _fake_key_and_results()
    real_key = {"session": "session_test", "pairs": [
        dict(v, left={**v["left"], "provenance": "AI" if v["ai_side"] == "left" else "family"},
             right={**v["right"], "provenance": "family" if v["ai_side"] == "left" else "AI"})
        for v in key_by_pair.values()]}
    results_path = _write_session(tmp_path, real_key, results)
    stats = bf.run_score(results_path)
    assert stats["detection_rate"] is not None and abs(stats["detection_rate"] - 2 / 3) < 1e-4
    assert "session_test" in capsys.readouterr().out


def test_score_bad_json_human_error(bf, tmp_path, capsys):
    """m-6：results.json 损坏 → 人话报错 exit 2，不裸栈。"""
    sdir = tmp_path / "session_bad"
    sdir.mkdir()
    (sdir / "key.json").write_text("{}", encoding="utf-8")
    rp = sdir / "results.json"
    rp.write_text("{bad json", encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        bf.run_score(rp)
    assert ei.value.code == 2
    assert "无法解析" in capsys.readouterr().err


def test_score_bad_structure_human_error(bf, tmp_path):
    """m-6：results.json 结构不对（如误传 blind.json）→ 人话报错不裸栈。"""
    sdir = tmp_path / "session_struct"
    sdir.mkdir()
    (sdir / "key.json").write_text('{"session":"s","pairs":[]}', encoding="utf-8")
    rp = sdir / "results.json"
    rp.write_text('{"session":"s","pairs":[]}', encoding="utf-8")   # 没有 results 键
    with pytest.raises(SystemExit) as ei:
        bf.run_score(rp)
    assert ei.value.code == 2


def test_score_session_mismatch_warns_but_scores(bf, tmp_path, capsys):
    """m-6：results 与 key 的 session 错配 / ghost pair → 告警，仍对能对上的部分出统计。"""
    key_by_pair, results = _fake_key_and_results()
    real_key = {"session": "session_A", "pairs": list(key_by_pair.values())}
    results = results + [{"pair_id": "ghost", "choice": "left", "rubric": {}, "note": ""}]
    results_path = _write_session(tmp_path, real_key, results, session="session_B")
    stats = bf.run_score(results_path)
    err = capsys.readouterr().err
    assert "不一致" in err and "session_A" in err and "session_B" in err
    assert "无对应 pair" in err and "1 条" in err
    assert stats["results_not_in_key"] == 1
    assert stats["decisive"] == 3 and stats["correct"] == 2            # 能对上的部分照常统计
