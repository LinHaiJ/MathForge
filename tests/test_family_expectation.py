"""构造测试 · 期望族（packs/probability/families/expectation.py）。"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM = "expectation"
COMBOS = pl.load_pack("probability")["families"][FAM].GRID["p5"]


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("probability")
    assert FAM in pack["families"]
    return pack["families"][FAM]


def test_contract(mod):
    assert mod.KP_ID == "prob.expectation" and len(mod.GRID["p5"]) == 24


@pytest.mark.parametrize("i", range(24))
def test_grid(mod, i):
    x1, x2, x3, p1, p2 = COMBOS[i]
    q = mod.disc_expect(x1, x2, x3, p1, p2)
    assert q is not None
    for name, ok in q["assert"]:
        assert bool(ok), f"{i} 断言 {name} 未过"
    p3 = 1 - p1 - p2
    expect = x1 * p1 + x2 * p2 + x3 * p3
    assert sp.simplify(sp.sympify(q["answer_sympy"]) - expect) == 0
    assert q["statement_md"].count("P(X=") == 3


def test_rejects_non_normalized(mod):
    # p1+p2 > 1 → p3 < 0 → 拒绝
    assert mod.disc_expect(1, 2, 3, sp.Rational(3, 4), sp.Rational(1, 2)) is None
