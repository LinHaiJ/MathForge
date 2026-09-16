"""构造测试 · 二项分布族（packs/probability/families/binomial.py）。

20 组参数逐格断言（另 4 组因分母 128 超纪律剔除，族 docstring 已披露）。
"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM = "binomial"
COMBOS = pl.load_pack("probability")["families"][FAM].GRID["p5"]


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("probability")
    assert FAM in pack["families"]
    return pack["families"][FAM]


def test_contract(mod):
    assert mod.KP_ID == "prob.rv.discrete"
    assert 20 <= len(mod.GRID["p5"]) <= 24
    items = mod.enumerate_family(limit=24)
    assert 20 <= len(items) <= 24


@pytest.mark.parametrize("i", range(22))
def test_grid(mod, i):
    combo = COMBOS[i]
    q = mod.binom_prob(*combo)
    assert q is not None, f"{combo} 应合法"
    for name, ok in q["assert"]:
        assert bool(ok), f"{combo} 断言 {name} 未过"
    p = sp.Rational(q["params"]["p"])
    n = q["params"]["n"]
    answer = sp.sympify(q["answer_sympy"])
    mode = q["params"]["mode"]
    if mode == "exact":
        expect = sp.binomial(n, q["params"]["k"]) * p ** q["params"]["k"] * (1 - p) ** (n - q["params"]["k"])
    elif mode == "ge1":
        expect = 1 - (1 - p) ** n
    else:  # ge2
        expect = 1 - (1 - p) ** n - n * p * (1 - p) ** (n - 1)
    assert sp.simplify(answer - expect) == 0
    assert 0 < answer < 1


def test_mode_coverage(mod):
    items = mod.enumerate_family(limit=24)
    modes = {it["params"]["mode"] for it in items}
    assert modes == {"exact", "ge1", "ge2"}
    answers = {it["answer_sympy"] for it in items}
    assert len(answers) >= 15, "答案多样性过低（防背答案）"
