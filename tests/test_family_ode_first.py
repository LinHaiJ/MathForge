"""构造测试 · 一阶微分方程族（packs/calculus/families/ode_first.py）。"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM = "ode_first"
COMBOS = pl.load_pack("calculus")["families"][FAM].GRID["p3"]


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("calculus")
    assert FAM in pack["families"]
    return pack["families"][FAM]


def test_contract(mod):
    assert mod.KP_ID == "calc.ode.first" and len(mod.GRID["p3"]) == 24


@pytest.mark.parametrize("i", range(24))
def test_grid_dsolve_crosscheck(mod, i):
    p, q, a = COMBOS[i]
    inst = mod.ode1_linear(p, q, a)
    assert inst is not None, f"{(p,q,a)} 应合法"
    for name, ok in inst["assert"]:
        assert bool(ok), f"{(p,q,a)} 断言 {name} 未过"
    x = sp.Symbol("x")
    yy = sp.Function("y")
    sol = sp.dsolve(yy(x).diff(x) + p * yy(x) - q, yy(x), ics={yy(0): a})
    assert sp.simplify(sol.rhs - sp.sympify(inst["answer_sympy"])) == 0
    s = inst["statement_md"]
    assert "y'" in s and "y(0)" in s and "______" in s
    assert f"+ {p}y" not in s or p != 1  # 系数 1 不显示


def test_degenerate_flagged_and_excluded(mod):
    # a == q/p → 解恒为常数：族内 non_degenerate 断言标记为 False，枚举自动剔除
    inst = mod.ode1_linear(2, 2, 1)
    assert dict(inst["assert"])["non_degenerate"] is False
    assert all(it["params"] != {"p": 2, "q": 2, "a": 1}
               for it in mod.enumerate_family(limit=24))
