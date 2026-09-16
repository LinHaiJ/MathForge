"""构造测试 · 泰勒公式族（packs/calculus/families/taylor.py）。

24 组参数逐格断言：族断言全过 + 测试侧独立重推（sp.diff 直接求导 vs 族内 series）+ 题干卫生。
"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM_MODULE = "taylor"
COMBOS = [(f, k, m) for (f, k, m) in pl.load_pack("calculus")["families"][FAM_MODULE].GRID["p3"]]


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("calculus")
    assert FAM_MODULE in pack["families"]
    return pack["families"][FAM_MODULE]


def test_module_contract(mod):
    assert mod.KP_ID == "calc.taylor"
    assert mod.CACHE_NS == "family-calc.taylor:v1"
    assert len(mod.GRID["p3"]) == 24


@pytest.mark.parametrize("combo", COMBOS)
def test_grid_independent_derivative(mod, combo):
    func, k, mode = combo
    q = mod.taylor_mclaurin(func, k, mode)
    assert q is not None, f"{combo} 应合法"
    for name, ok in q["assert"]:
        assert bool(ok), f"{combo} 断言 {name} 未过"
    # 独立路径：直接 n 阶导数在 0 的值除以 k! == 系数（泰勒定义）
    f = sp.exp(sp.Symbol("x")) if func == "exp" else sp.sin(sp.Symbol("x"))
    deriv0 = sp.diff(f, sp.Symbol("x"), k).subs(sp.Symbol("x"), 0)
    expect = sp.simplify(deriv0 / sp.factorial(k))
    assert sp.simplify(sp.sympify(q["answer_sympy"]) - expect) == 0 \
        if mode == "coeff" else q["answer_sympy"] == sp.sstr(deriv0)


def test_coverage(mod):
    items = mod.enumerate_family(limit=24)
    assert {it["params"]["mode"] for it in items} == {"coeff", "deriv"}
    assert {it["params"]["func"] for it in items} == {"exp", "sin"}


def test_statement_hygiene(mod):
    for it in mod.enumerate_family(limit=24):
        s = it["statement_md"]
        assert s and "______" in s
        for banned in ("201", "202", "真题", "考研题"):
            assert banned not in s
