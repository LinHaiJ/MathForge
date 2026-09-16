# AI 变式引擎闸门单测（任务书 P1-a 验收 · 2026-09-15）
# 全部不调 API：llm.chat_json 用 monkeypatch 替身。MATHFORGE_DEMO 由 conftest 强制为 1。

import pytest

from v2 import ai_variant as av

KERNEL = {
    "kp": "渐近线", "family": "asy_slant",
    "params": {"c": 1, "r": 1, "k": 1, "mode": "slant"},
    "difficulty": "基础",
    "statement_md": "曲线 $y = x + 1 + \\frac{1}{x - 1}$ 的斜渐近线方程为 $y =$ ______。",
    "answer_sympy": "x + 1",
    "analysis": "x→∞ 时余项 1/(x−1) → 0，故斜渐近线为 y = x + 1。",
}


# ---- G1 结构 ----
def test_g1_ok():
    v = {"kind": "contextual",
         "stem_md": "某物体位移 $s(t)=t+1+\\\\frac{1}{t-1}$（$t>1$，单位：米、秒），"
                    "当 $t\\\\to\\\\infty$ 时位移曲线的斜渐近线方程为 $s=$ ______。",
         "answer_expr": "x + 1"}
    ok, why = av.gate_structure(v)
    assert ok, why


def test_g1_missing_field():
    ok, why = av.gate_structure({"kind": "contextual", "stem_md": "x" * 60})
    assert not ok and "answer_expr" in why


def test_g1_kind_forbidden():
    v = {"kind": "reworded", "stem_md": "x" * 60, "answer_expr": "x + 1"}
    ok, why = av.gate_structure(v)
    assert not ok and "非核不变型" in why


def test_g1_bad_dollar():
    v = {"kind": "contextual", "stem_md": "x" * 50 + "$", "answer_expr": "x + 1"}
    ok, why = av.gate_structure(v)
    assert not ok and "$" in why


def test_g1_multi_blank_rejected():
    """单空纪律：三问三空的 multistep 题面在闸门拦截（2026-09-16 用户实测缺陷）。"""
    stem = ("已知曲线 $y = x + 1 + \\\\frac{1}{x - 1}$，先求 $\\\\lim_{x\\\\to\\\\infty} f(x)$ 的值 ______，"
            "再求斜渐近线方程 ______。（单位说明补充到长度合规：米，秒，千克，帕斯卡与摄氏度。）")
    v = {"kind": "multistep", "stem_md": stem, "answer_expr": "x + 1"}
    ok, why = av.gate_structure(v)
    assert not ok and "单空" in why


# ---- G2 数学保真 ----
def test_g2_exact():
    ok, why = av.gate_math({"answer_expr": "x + 1"}, KERNEL)
    assert ok and why == "等价"


def test_g2_isomorphic_rename():
    ok, why = av.gate_math({"answer_expr": "t + 1"}, KERNEL)
    assert ok and "同构" in why


def test_g2_drift_rejected():
    ok, why = av.gate_math({"answer_expr": "2"}, KERNEL)
    assert not ok and "漂移" in why


def test_g2_fraction_form():
    ok, _ = av.gate_math({"answer_expr": "1/4"}, {"answer_sympy": "Rational(1,4)"})
    assert ok


# ---- G3 多样性 ----
def test_g3_too_close_to_kernel():
    ok, why = av.gate_diverse(KERNEL["statement_md"], KERNEL["statement_md"], [])
    assert not ok and "换壳失败" in why


def test_g3_sibling_dup_rejected():
    sib = "某物体做直线运动，位移满足给定关系，求其渐近线方程，并说明理由与出处。"
    ok, why = av.gate_diverse(sib, KERNEL["statement_md"], [sib])
    assert not ok and "重复" in why


def test_g3_ok():
    ok, why = av.gate_diverse("篮球运动员罚球命中率恒定，连续罚球四次恰命中一次的概率是多少？",
                              KERNEL["statement_md"], [])
    assert ok, why


# ---- 主流程（LLM 替身） ----
def _stub_chat(plan_ok=True, variants=None, fail=False):
    class _Stub:
        def __call__(self, messages, **kw):
            if fail:
                raise RuntimeError("network down")
            if messages[0]["content"].startswith("你是考研数学命题组长"):
                if not plan_ok:
                    return {"ok": False}
                return {"assess": "考斜渐近线的定义与求法", "approach": "取极限差为零", "trap": "忘验竖直", "ok": True}
            return {"variants": variants or []}
    return _Stub()


def test_mainflow_plan_rejected(monkeypatch):
    monkeypatch.setattr("llm.chat_json", _stub_chat(plan_ok=False))
    res = av.make_ai_variants(KERNEL, k=2)
    assert res["ok"] is False and res["stage"] == "plan"


def test_mainflow_llm_failure(monkeypatch):
    monkeypatch.setattr("llm.chat_json", _stub_chat(fail=True))
    res = av.make_ai_variants(KERNEL, k=2)
    assert res["ok"] is False and res["stage"] in ("plan", "generate")


def test_mainflow_all_rejected(monkeypatch):
    bad = {"kind": "contextual", "stem_md": KERNEL["statement_md"], "answer_expr": "x + 1"}
    monkeypatch.setattr("llm.chat_json", _stub_chat(variants=[bad]))
    res = av.make_ai_variants(KERNEL, k=1)
    assert res["ok"] is False and res["stats"]["rejected"] >= 1


def test_mainflow_pass(monkeypatch):
    good = {
        "kind": "contextual",
        "stem_md": "一架无人机的高度满足 $h(t)=t+1+\\\\frac{1}{t-1}$（$t>1$，单位：米）。"
                   "当 $t\\\\to\\\\infty$ 时，高度曲线存在一条斜渐近线，请写出该渐近线的方程：$h=$ ______。",
        "answer_expr": "x + 1",
        "note": "物理情境包装",
    }
    monkeypatch.setattr("llm.chat_json", _stub_chat(variants=[good]))
    res = av.make_ai_variants(KERNEL, k=1)
    assert res["ok"] is True and res["stats"]["passed"] == 1
    assert res["variants"][0]["g2_kind"] in ("exact", "isomorphic")
