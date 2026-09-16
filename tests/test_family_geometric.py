"""构造测试 · 几何概型族（packs/probability/families/geometric.py）。"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM = "geometric"
CS = pl.load_pack("probability")["families"][FAM].GRID["c"]


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("probability")
    assert FAM in pack["families"]
    return pack["families"][FAM]


def test_contract(mod):
    assert mod.KP_ID == "prob.geometric" and len(mod.GRID["c"]) == 24


@pytest.mark.parametrize("i", range(24))
def test_grid(mod, i):
    c = CS[i]
    q = mod.geo_square_line(c)
    assert q is not None
    for name, ok in q["assert"]:
        assert bool(ok), f"c={c} 断言 {name} 未过"
    answer = sp.sympify(q["answer_sympy"])
    # 独立路径：二重积分
    x, y = sp.symbols("x y")
    expect = sp.integrate(sp.integrate(1, (y, 0, c - x)), (x, 0, c))
    assert sp.simplify(answer - expect) == 0
    assert 0 < answer <= sp.Rational(1, 2)


def test_out_of_range_rejected(mod):
    assert mod.geo_square_line(sp.Rational(3, 2)) is None  # 超界需分段
