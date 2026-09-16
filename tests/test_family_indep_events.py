"""构造测试 · 多事件独立性族（packs/probability/families/indep_events.py）。

24 组参数逐格断言：族断言全过 + 测试侧独立重推（容斥展开）+ fobar 反解唯一 + 题干卫生。
"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM_MODULE = "indep_events"
COMBOS = pl.load_pack("probability")["families"][FAM_MODULE].GRID["p3"]


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("probability")
    assert FAM_MODULE in pack["families"]
    return pack["families"][FAM_MODULE]


def test_module_contract(mod):
    assert mod.KP_ID == "prob.independence"
    assert mod.CACHE_NS == "family-prob.independence:v1"
    assert len(mod.GRID["p3"]) == 24


@pytest.mark.parametrize("i", range(24))
def test_grid_inclusion_exclusion(mod, i):
    a, b, c = COMBOS[i]
    q = mod.indep3_add(a, b, c)
    assert q is not None, f"{a},{b},{c} 应合法"
    for name, ok in q["assert"]:
        assert bool(ok), f"({a},{b},{c}) 断言 {name} 未过"
    answer = sp.sympify(q["answer_sympy"])
    # 独立路径：容斥展开（与族内补集积不同代码路径）
    expect = (a + b + c) - (a * b + a * c + b * c) + a * b * c
    assert sp.simplify(answer - expect) == 0
    assert 0 < answer < 1
    s = q["statement_md"]
    assert "相互独立" in s and "______" in s
    for banned in ("201", "202", "真题", "考研题"):
        assert banned not in s


def test_duplicate_probs_rejected(mod):
    assert mod.indep3_add(sp.Rational(1, 2), sp.Rational(1, 2), sp.Rational(1, 3)) is None
    assert mod.indep3_add(sp.Rational(1, 1), sp.Rational(1, 3), sp.Rational(1, 4)) is None
