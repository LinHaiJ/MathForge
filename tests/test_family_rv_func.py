"""构造测试 · 随机变量函数分布族（packs/probability/families/rv_func.py）。

任务书 §5 步骤 4「24/24 式确定性枚举」：72 全格等距取 24 实例逐格断言，
无随机、无 LLM。每格多重校验：
  1. 族内合法性断言全过（归一性 / t0∈支撑集 / 独立路径 / 干净度 / fobar）
  2. 独立符号路径（测试内重推，不信族内结果）：
     密度 f_Y(t0) = f_X((t0-d)/c)·(1/c)；CDF P(Y≤t0) = ∫_a^{(t0-d)/c} f_X
  3. 独立数值路径：闭式浮点代入一致（1e-12）
  4. 题干非空、含「均匀分布」、不含任何真题来源标记
"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM_MODULE = "rv_func"

# 模块加载期即做一次枚举（与 /v2/turn 运行时同一代码路径）
_PACK = pl.load_pack("probability")
_MOD = _PACK["families"][FAM_MODULE]
ITEMS = _MOD.enumerate_family(limit=24)
assert len(ITEMS) == 24, "枚举应产出 24 实例"


@pytest.fixture(scope="module")
def mod():
    assert _MOD.KP_ID == "prob.rv.func"
    return _MOD


def test_module_contract(mod):
    assert mod.KP_NAME == "随机变量函数分布"
    assert mod.CACHE_NS == "family-prob.rv.func:v1"
    assert mod.FAMILY in mod.SPEC
    assert mod.PACK_ID == "probability"
    assert mod.GRID == {"a": [0, 1], "L": [1, 2, 3, 4], "c": [1, 2, 3], "m": [1, 2, 3]}


@pytest.mark.parametrize("idx", range(24))
def test_grid_asserts(idx):
    q = ITEMS[idx]
    names = [n for n, _ in q["assert"]]
    assert names == ["support_valid", "t_in_support", "normalization",
                     "independent_path", "clean_answer", "fobar_invertible"]
    for name, ok in q["assert"]:
        assert bool(ok), f"实例#{idx} {q['params']} 断言 {name} 未过"


@pytest.mark.parametrize("idx", range(24))
def test_independent_symbolic_path(idx):
    """测试侧独立重推（与族内实现不同的代码路径）。"""
    q = ITEMS[idx]
    a, L, c, m = (q["params"][k] for k in ("a", "L", "c", "m"))
    d = q["params"]["d"]
    t0 = c * a + d + sp.Rational(m * c * L, 4)
    answer = sp.sympify(q["answer_sympy"])
    x = sp.symbols("x")
    x_cut = sp.Rational(t0 - d, c)
    assert a < x_cut < a + L, "逆变换点应落在 X 支撑集内"
    if q["params"]["mode"] == "density":
        expect = (sp.Rational(1, L)) / c          # f_X(x_cut)·(1/c)
    else:
        expect = sp.integrate(sp.Rational(1, L), (x, a, x_cut))
    assert sp.simplify(answer - expect) == 0


@pytest.mark.parametrize("idx", range(24))
def test_numeric_path(idx):
    q = ITEMS[idx]
    a, L, c, m = (q["params"][k] for k in ("a", "L", "c", "m"))
    d = q["params"]["d"]
    answer = float(sp.sympify(q["answer_sympy"]))
    t0 = c * a + d + m * c * L / 4
    if q["params"]["mode"] == "density":
        assert abs(answer - 1.0 / (c * L)) < 1e-12
    else:
        assert abs(answer - (t0 - (c * a + d)) / (c * L)) < 1e-12


def test_mode_coverage():
    """24 实例应同时覆盖 density 与 cdf 两种问法，且答案 ≥8 种（防背答案）。"""
    modes = {it["params"]["mode"] for it in ITEMS}
    assert modes == {"density", "cdf"}
    answers = {it["answer_sympy"] for it in ITEMS}
    assert len(answers) >= 8, f"答案多样性过低：{sorted(answers)}"


def test_statement_hygiene():
    for it in ITEMS:
        s = it["statement_md"]
        assert s and "均匀分布" in s and "______" in s
        assert "$" in s
        for banned in ("201", "202", "真题", "考研题"):
            assert banned not in s, f"题干疑似引用真题标记: {banned}"
