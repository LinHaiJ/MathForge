"""构造测试 · 条件概率族（packs/probability/families/condition.py）。"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM = "condition"
COMBOS = pl.load_pack("probability")["families"][FAM].GRID["p2"]


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("probability")
    assert FAM in pack["families"]
    return pack["families"][FAM]


def test_contract(mod):
    assert mod.KP_ID == "prob.condition" and len(mod.GRID["p2"]) == 24


@pytest.mark.parametrize("i", range(24))
def test_grid(mod, i):
    a, w = COMBOS[i]
    q = mod.cond_mult(a, w)
    assert q is not None
    for name, ok in q["assert"]:
        assert bool(ok), f"{i} 断言 {name} 未过"
    assert sp.simplify(sp.sympify(q["answer_sympy"]) - a * w) == 0


def test_rejects_zero_prior(mod):
    assert mod.cond_mult(0, sp.Rational(1, 2)) is None
