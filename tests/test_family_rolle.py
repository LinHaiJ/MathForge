"""构造测试 · 罗尔定理族（packs/calculus/families/rolle.py，D8-6 包内化）。

委托 v1 repo 根 families 的 rolle_xi 族完成枚举，本测试仅校验：包内族模块契约、
kp 入包图、24 上限枚举的确定性计数与参数互异性、契约字段齐备、断言纪律全过、
answer_sympy 可解析且 simplify 不异常、无退化标记。无随机、无 LLM 零调用。
"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM_MODULE = "rolle"
PACK_ID = "calculus"
KP_ID = "calc.rolle"
KP_NAME = "罗尔定理"
CACHE_NS = "family-calc.rolle:v1"
FAMILY = "rolle_xi"
EXPECTED = 12          # 委托 v1 参数格的确定合法题数
MATRIX_ANS = False     # 答案为数值


def _parse(s):
    return sp.sympify(s, locals={"Matrix": sp.Matrix}) if MATRIX_ANS else sp.sympify(s)


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack(PACK_ID)
    assert FAM_MODULE in pack["families"], "族文件未被 pack_loader 装载"
    return pack["families"][FAM_MODULE]


def test_module_contract(mod):
    assert mod.KP_ID == KP_ID
    assert mod.KP_NAME == KP_NAME
    assert mod.CACHE_NS == CACHE_NS
    assert mod.FAMILY == FAMILY
    assert mod.FAMILY in mod.SPEC


def test_kp_in_pack_graph():
    pack = pl.load_pack(PACK_ID)
    kp = pl.get_kp(pack, KP_ID)
    assert kp is not None and kp.get("verifiable") is True


def test_enumerate_full_grid(mod):
    items = mod.enumerate_family(limit=24)
    assert len(items) == EXPECTED, f"期望 {EXPECTED} 题，实得 {len(items)}"

    keys = {tuple(sorted(it["params"].items())) for it in items}
    assert len(keys) == len(items), "参数组合非互异"
    if EXPECTED >= 8:
        assert len(keys) > 8, "参数互异应 >8 组"

    for it in items:
        for f in ("kp_id", "family", "params", "statement_md",
                  "answer_sympy", "difficulty", "assert", "verify_level"):
            assert f in it, f"缺字段 {f}"
        assert it["kp_id"] == KP_ID
        assert it["family"] == FAMILY
        assert it["statement_md"].strip()
        assert it["verify_level"] == "green"
        assert "degenerate" not in it, "出现退化标记"

        fails = [n for n, ok in it["assert"] if not bool(ok)]
        assert not fails, f"断言失败 {fails}"

        ans = _parse(it["answer_sympy"])
        simp = sp.simplify(ans)
        assert simp is not None


def test_deterministic_repeat(mod):
    a = mod.enumerate_family(limit=24)
    b = mod.enumerate_family(limit=24)
    assert [i["answer_sympy"] for i in a] == [i["answer_sympy"] for i in b]
