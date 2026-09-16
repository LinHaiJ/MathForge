"""构造测试 · 全概率族（packs/probability/families/total_bayes.py）。"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM = "total_bayes"
COMBOS = pl.load_pack("probability")["families"][FAM].GRID["p3"]


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("probability")
    assert FAM in pack["families"]
    return pack["families"][FAM]


def test_contract(mod):
    assert mod.KP_ID == "prob.total_bayes" and len(mod.GRID["p3"]) == 24


@pytest.mark.parametrize("i", range(24))
def test_grid(mod, i):
    p, q1, q2 = COMBOS[i]
    q = mod.bayes2_total(p, q1, q2)
    assert q is not None
    for name, ok in q["assert"]:
        assert bool(ok), f"{i} 断言 {name} 未过"
    answer = sp.sympify(q["answer_sympy"])
    # 独立路径：条件概率加权定义
    expect = p * q1 + (1 - p) * q2
    assert sp.simplify(answer - expect) == 0
    assert 0 < answer < 1


def test_rejects_equal_conditionals(mod):
    assert mod.bayes2_total(sp.Rational(1, 2), sp.Rational(1, 2), sp.Rational(1, 2)) is None
