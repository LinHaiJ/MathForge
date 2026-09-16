"""构造测试 · 协方差族（packs/probability/families/covariance.py）。"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM = "covariance"
COMBOS = pl.load_pack("probability")["families"][FAM].GRID["p5"]


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("probability")
    assert FAM in pack["families"]
    return pack["families"][FAM]


def test_contract(mod):
    assert mod.KP_ID == "prob.cov.corr" and len(mod.GRID["p5"]) == 24


@pytest.mark.parametrize("i", range(24))
def test_grid_dual_path(mod, i):
    x1, y1, x2, y2, p = COMBOS[i]
    q = mod.cov2pt(x1, y1, x2, y2, p)
    assert q is not None
    for name, ok in q["assert"]:
        assert bool(ok), f"{i} 断言 {name} 未过"
    # 独立路径（测试侧重推）：定义式 Σ(x−EX)(y−EY)p
    pp = sp.Rational(p)
    ex = x1 * pp + x2 * (1 - pp)
    ey = y1 * pp + y2 * (1 - pp)
    expect = (x1 - ex) * (y1 - ey) * pp + (x2 - ex) * (y2 - ey) * (1 - pp)
    assert sp.simplify(sp.sympify(q["answer_sympy"]) - expect) == 0
