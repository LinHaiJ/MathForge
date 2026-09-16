"""构造测试 · 方差族（packs/probability/families/variance.py）。"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM = "variance"
COMBOS = pl.load_pack("probability")["families"][FAM].GRID["p5"]


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("probability")
    assert FAM in pack["families"]
    return pack["families"][FAM]


def test_contract(mod):
    assert mod.KP_ID == "prob.variance" and len(mod.GRID["p5"]) == 24


@pytest.mark.parametrize("i", range(24))
def test_grid_dual_path(mod, i):
    x1, x2, x3, p1, p2 = COMBOS[i]
    q = mod.disc_var(x1, x2, x3, p1, p2)
    assert q is not None
    for name, ok in q["assert"]:
        assert bool(ok), f"{i} 断言 {name} 未过"
    # 独立路径（测试侧重推）：定义式
    p3 = 1 - p1 - p2
    xs, ps = (x1, x2, x3), (p1, p2, p3)
    e = sum(x * p for x, p in zip(xs, ps))
    expect = sum((x - e) ** 2 * p for x, p in zip(xs, ps))
    assert sp.simplify(sp.sympify(q["answer_sympy"]) - expect) == 0
    assert q["statement_md"].count("P(X=") == 3 and "D(X)" in q["statement_md"]


def test_nonnormalized_rejected(mod):
    assert mod.disc_var(1, 2, 3, sp.Rational(3, 4), sp.Rational(1, 2)) is None
