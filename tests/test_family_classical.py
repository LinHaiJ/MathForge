"""构造测试 · 古典概型族（packs/probability/families/classical.py）。"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM = "classical"
COMBOS = pl.load_pack("probability")["families"][FAM].GRID["p4"]


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("probability")
    assert FAM in pack["families"]
    return pack["families"][FAM]


def test_contract(mod):
    assert mod.KP_ID == "prob.classical" and len(mod.GRID["p4"]) == 24


@pytest.mark.parametrize("i", range(24))
def test_grid(mod, i):
    N, k, n, m = COMBOS[i]
    q = mod.hypergeo(N, k, n, m)
    assert q is not None
    for name, ok in q["assert"]:
        assert bool(ok), f"{i} 断言 {name} 未过"
    answer = sp.sympify(q["answer_sympy"])
    expect = sp.binomial(k, m) * sp.binomial(N - k, n - m) / sp.binomial(N, n)
    assert sp.simplify(answer - expect) == 0
    assert 0 < answer <= 1


def test_infeasible_rejected(mod):
    assert mod.hypergeo(6, 2, 3, 3) is None   # m > k
    assert mod.hypergeo(6, 2, 2, 0) is not None
