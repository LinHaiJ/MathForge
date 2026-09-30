"""构造测试 · 罗尔定理族 rolle_roots（packs/calculus/families/rolle.py，2026-09-29 问法重锚）。

v2 代次（CACHE_NS family-calc.rolle:v2）：罗尔承重型问法——f 有 4 个互异整数零点，
问 f'(x)=0 的（不同）实根个数（罗尔每开区间 ≥1 根 + 三次导函数 ≤3 根，构造即正确），
替换旧 rolle_xi「解 ξ」装饰性问法。旧族在 repo 根 families.py 原样保留（v1 线零改动），
本文件另设 v1 兼容抽测。

校验面：
  - 模块契约 / kp 入包图 / 参数格互异与 _clean 纪律（|根|≤5、|k|≤3 非零）；
  - 双问法齐备（ask=count/interval 两种问法、答案恒 3）、去参模板 ≥2（§5 合格线）；
  - 断言纪律全过 + render_flaws 干净（generate 守卫清单，与 v2api 家族直出守卫同源）；
  - 数学核独立复核（count_roots 从 params 重推导）；
  - v2 家族直出集成：/v2/turn 对 calc.rolle 出新问法题，verify_level=green，
    假 LLM 炸弹不触发（零 LLM 证据）；
  - v1 兼容：root families.enumerate_family("rolle_xi") 行为不变（问法/题数均不变）。
无随机、无 LLM 零调用（假炸弹锁定）、零出网。
"""

import os
import re
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402
from generate import render_flaws  # noqa: E402

FAM_MODULE = "rolle"
PACK_ID = "calculus"
KP_ID = "calc.rolle"
KP_NAME = "罗尔定理"
CACHE_NS = "family-calc.rolle:v2"
FAMILY = "rolle_roots"
LEGACY_FAMILY = "rolle_xi"
EXPECTED = 16          # 手选参数格全合法（含双问法交替、正负 k、全正/全负/跨零根）

COUNT_MARK = "在实数范围内不同实根的个数"   # 问法一（主）特征句
INTERVAL_MARK = "不同实根个数"              # 问法二（区间）特征句（与问法一同含"不同实根"）


def _mask(s: str) -> str:
    """去参模板：数字 → #（与 v2/ai_variant._mask 同口径，G3 多样性基准）。"""
    return re.sub(r"-?\d+(?:\.\d+)?", "#", s or "").strip()


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack(PACK_ID)
    assert FAM_MODULE in pack["families"], "族文件未被 pack_loader 装载"
    return pack["families"][FAM_MODULE]


@pytest.fixture(scope="module")
def items(mod):
    return mod.enumerate_family(limit=24)


# --------------------------------------------------------------------------- #
# 模块契约 / 参数格纪律
# --------------------------------------------------------------------------- #
def test_module_contract(mod):
    assert mod.KP_ID == KP_ID
    assert mod.KP_NAME == KP_NAME
    assert mod.CACHE_NS == CACHE_NS                    # 问法重锚代次 bump v1→v2
    assert mod.FAMILY == FAMILY
    assert mod.FAMILY in mod.SPEC
    assert mod.LEGACY_FAMILY == LEGACY_FAMILY          # v1 兼容入口保留
    assert LEGACY_FAMILY in mod.SPEC


def test_kp_in_pack_graph():
    pack = pl.load_pack(PACK_ID)
    kp = pl.get_kp(pack, KP_ID)
    assert kp is not None and kp.get("verifiable") is True


def test_enumerate_full_grid(items):
    assert len(items) == EXPECTED, f"期望 {EXPECTED} 题，实得 {len(items)}"

    keys = {(tuple(it["params"]["roots"]), it["params"]["k"]) for it in items}
    assert len(keys) == EXPECTED, "参数组合（根/系数）非互异"

    for it in items:
        for f in ("kp_id", "kp", "family", "params", "statement_md", "analysis",
                  "answer_sympy", "difficulty", "assert", "verify_level"):
            assert f in it, f"缺字段 {f}"
        assert it["kp_id"] == KP_ID
        assert it["kp"] == KP_NAME
        assert it["family"] == FAMILY
        assert it["statement_md"].strip() and it["analysis"].strip()
        assert it["verify_level"] == "green"
        assert "degenerate" not in it, "出现退化标记"
        assert it["params"]["ask"] in ("count", "interval")

        fails = [n for n, ok in it["assert"] if not bool(ok)]
        assert not fails, f"断言失败 {fails}"


def test_clean_params_discipline(items):
    """_clean 纪律：4 根互异整数 |r|≤5 严格递增、k 非零 |k|≤3；正负 k 与根覆盖都在场。"""
    seen_k, seen_neg_root, seen_pos_root, seen_zero_root = set(), False, False, False
    for it in items:
        roots = it["params"]["roots"]
        k = it["params"]["k"]
        assert len(roots) == 4 and all(isinstance(r, int) for r in roots)
        assert all(abs(r) <= 5 for r in roots), f"根超格 {roots}"
        assert roots[0] < roots[1] < roots[2] < roots[3], f"根非严格递增 {roots}"
        assert isinstance(k, int) and k != 0 and abs(k) <= 3
        seen_k.add(k)
        seen_neg_root |= any(r < 0 for r in roots)
        seen_pos_root |= any(r > 0 for r in roots)
        seen_zero_root |= 0 in roots
    assert 1 in seen_k and -1 in seen_k and 2 in seen_k and 3 in seen_k
    assert seen_neg_root and seen_pos_root and seen_zero_root, "参数格未覆盖正负/跨零"


def test_deterministic_repeat(mod):
    a = mod.enumerate_family(limit=24)
    b = mod.enumerate_family(limit=24)
    assert [i["statement_md"] for i in a] == [i["statement_md"] for i in b]
    assert [i["answer_sympy"] for i in a] == [i["answer_sympy"] for i in b]


# --------------------------------------------------------------------------- #
# 双问法 / 去参模板 / 渲染卫生
# --------------------------------------------------------------------------- #
def test_dual_question_forms(items):
    """双问法（§5 家族纪律）：count 主问法 + interval 区间问法，答案恒 3。"""
    by_ask = {}
    for it in items:
        by_ask.setdefault(it["params"]["ask"], []).append(it)
    assert set(by_ask) == {"count", "interval"}, f"双问法缺臂 {set(by_ask)}"
    assert all(len(v) >= 4 for v in by_ask.values()), "每种问法至少 4 格"

    for it in by_ask["count"]:
        assert COUNT_MARK in it["statement_md"]
        assert INTERVAL_MARK not in it["statement_md"], "count 问法不得串入 interval 特征句"
    for it in by_ask["interval"]:
        assert INTERVAL_MARK in it["statement_md"]
        assert COUNT_MARK not in it["statement_md"], "interval 问法不得串入 count 特征句"
        roots = it["params"]["roots"]
        span = f"({roots[0]},{roots[-1]})"
        assert span in it["statement_md"].replace(" ", ""), f"区间端点未入题干 {span}"

    assert {it["answer_sympy"] for it in items} == {"3"}, "两问法答案恒为 3"


def test_deparam_templates_ge2(items):
    """去参模板 ≥2（AGENTS §5 / packs/CONTRACT 合格线）：两种问法 → 模板集互异。"""
    tmpl = {_mask(it["statement_md"]) for it in items}
    assert len(tmpl) >= 2, "去参模板不足 2 种"
    # 两问法的模板集不相交（问句本身不同，非仅参数数字不同）
    by_ask_tmpl = {}
    for it in items:
        by_ask_tmpl.setdefault(it["params"]["ask"], set()).add(_mask(it["statement_md"]))
    assert len(by_ask_tmpl) == 2
    a, b = by_ask_tmpl.values()
    assert a.isdisjoint(b), "两问法去参模板应互不相交"


def test_render_flaws_clean(items):
    """题干与解析均过 generate.render_flaws 守卫（与 v2api._try_pack_family 同源）。"""
    for it in items:
        assert render_flaws(it["statement_md"]) is None, it["statement_md"]
        assert render_flaws(it["analysis"]) is None, it["analysis"]


# --------------------------------------------------------------------------- #
# 数学核独立复核（从 params 重推导，不信任族内断言结果）
# --------------------------------------------------------------------------- #
def test_math_core_count_roots(items):
    x = sp.Symbol("x")
    for it in items:
        roots, k = it["params"]["roots"], it["params"]["k"]
        f = k * sp.prod([x - r for r in roots])
        P, dP = sp.Poly(f, x), sp.Poly(sp.diff(f, x), x)
        assert P.count_roots() == 4, "f 应恰 4 个互异实零点（罗尔前提）"
        assert dP.count_roots() == 3, "f'(x)=0 应恰 3 个实根"
        assert [dP.count_roots(roots[i], roots[i + 1]) for i in range(3)] == [1, 1, 1]
        assert dP.count_roots(-sp.oo, roots[0]) == 0
        assert dP.count_roots(roots[-1], sp.oo) == 0
        assert sp.sympify(it["answer_sympy"]) == 3


# --------------------------------------------------------------------------- #
# v2 家族直出集成（零 LLM：假炸弹不触发）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MATHFORGE_V2_DB", str(tmp_path / "rolle_v2_test.db"))
    monkeypatch.setenv("MATHFORGE_PROFILES_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    import app as app_mod

    with TestClient(app_mod.app) as c:
        yield c


def _llm_bomb(monkeypatch):
    """假 LLM 炸弹：家族直出路径任何 LLM 调用都应视为测试失败。"""
    def _boom(*a, **kw):  # noqa: ANN002, ANN003
        raise AssertionError("家族直出路径不得触发 LLM")

    import generate as _gen
    import llm as _llm
    monkeypatch.setattr(_llm, "chat", _boom)
    monkeypatch.setattr(_llm, "chat_json", _boom)
    monkeypatch.setattr(_gen, "chat_json", _boom)   # generate.py 以 from llm import chat_json 引用


def test_v2_turn_rolle_new_form_zero_llm(client, monkeypatch):
    """/v2/turn 对 calc.rolle：家族直出 rolle_roots 新问法，verify_level=green，零 LLM。"""
    _llm_bomb(monkeypatch)
    r = client.post("/v2/turn", json={"kp_id": KP_ID, "qtype": "fill",
                                      "difficulty": "基础"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True, d
    assert d["route"]["pack_family"] is True
    q = d["question"]
    assert "不同实根" in q["statement_md"] and "f'(x)=0" in q["statement_md"]
    assert "在开区间" not in q["statement_md"], "不应再出现旧 rolle_xi 装饰性问法"
    assert q["family"] == FAMILY
    assert q["routed"] == "family"
    assert q["verify_level"] == "green"
    assert q["qtype"] == "fill" and q["gen_qtype"] == "calculation"


def test_v2_pack_family_direct(mod):
    """直调 v2api._try_pack_family（v2api 家族路径函数）：命中 rolle_roots 新实例。"""
    import v2api

    pack = pl.load_pack(PACK_ID)
    q = v2api._try_pack_family(pack, KP_ID, seed=3)
    assert q is not None
    assert q["family"] == FAMILY and q["routed"] == "family"
    assert q["verify_level"] == "green"
    assert "不同实根" in q["statement_md"]
    assert q["question_fp"]  # 指纹非空（family+params 去重键）


# --------------------------------------------------------------------------- #
# v1 兼容：root families.rolle_xi 行为不变（问法/题数/答案均不变）
# --------------------------------------------------------------------------- #
def test_v1_rolle_xi_untouched(mod):
    import families as root_families

    legacy = root_families.enumerate_family(LEGACY_FAMILY, limit=24)
    assert len(legacy) == 12, "v1 root rolle_xi 参数格题数必须不变（12）"
    first = legacy[0]
    assert first["family"] == LEGACY_FAMILY
    # 旧问法特征：f(x)=a(x-p)(x-q)，求 f'(x)=0 在开区间 (p,q) 内的解 ξ（装饰性问法原样）
    assert "在开区间" in first["statement_md"] and "\\xi=" in first["statement_md"]
    a, p, q = (first["params"][k] for k in ("a", "p", "q"))
    assert sp.sympify(first["answer_sympy"]) == sp.Rational(p + q, 2)
    # pack 模块兼容委托入口与 root 直连一致（同代次同产出）
    via_pack = mod.enumerate_family(LEGACY_FAMILY, limit=24)
    assert [i["statement_md"] for i in via_pack] == [i["statement_md"] for i in legacy]
