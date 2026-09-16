"""构造测试 · 切线方程族（packs/calculus/families/tangent.py）。"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM = "tangent"
COMBOS = pl.load_pack("calculus")["families"][FAM].GRID["p3"]


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("calculus")
    assert FAM in pack["families"]
    return pack["families"][FAM]


def test_contract(mod):
    assert mod.KP_ID == "calc.derivative.def" and len(mod.GRID["p3"]) == 24


@pytest.mark.parametrize("i", range(24))
def test_grid(mod, i):
    kn, kd, c = COMBOS[i]
    q = mod.tangent_cubic(kn, kd, c)
    assert q is not None
    for name, ok in q["assert"]:
        assert bool(ok), f"{i} 断言 {name} 未过"
    # 独立路径：切线在切点处的函数值与导数值双吻合
    x = sp.Symbol("x")
    k = sp.Rational(kn, kd)
    tangent = sp.sympify(q["answer_sympy"])
    f = k * x**3
    assert sp.simplify(tangent.subs(x, c) - f.subs(x, c)) == 0
    assert sp.simplify(sp.diff(tangent, x).subs(x, c) - sp.diff(f, x).subs(x, c)) == 0
    s = q["statement_md"]
    assert "切线" in s and "______" in s and "1x^3" not in s


def test_zero_abscissa_rejected(mod):
    assert mod.tangent_cubic(1, 1, 0) is None
