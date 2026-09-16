"""构造测试 · 高阶线性微分方程族（packs/calculus/families/ode_high.py）。

任务书 §5 步骤 4「24/24 式确定性枚举」：24 组参数逐格断言，无随机、无 LLM。
每格多重校验：
  1. 族内合法性断言全过（特征根/解代入/初值/定常数千净/非退化）
  2. 独立符号路径：sp.dsolve（含 ics）与族构造解 simplify 等价（互证）
  3. 题干非空、含方程与初值、不含任何真题来源标记
"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM_MODULE = "ode_high"


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("calculus")
    assert FAM_MODULE in pack["families"], "族文件未被 pack_loader 装载"
    return pack["families"][FAM_MODULE]


def test_module_contract(mod):
    assert mod.KP_ID == "calc.ode.high"
    assert mod.KP_NAME == "高阶线性微分方程"
    assert mod.CACHE_NS == "family-calc.ode.high:v1"
    assert mod.FAMILY in mod.SPEC
    assert len(mod.GRID["p4"]) == 24


@pytest.mark.parametrize("combo", [(1, 2, 1, 0), (2, 1, 0, 1), (1, -1, 1, 1), (-1, 1, 2, 0),
                                   (1, 2, 0, 2), (2, -1, 1, 1), (-1, -2, 1, 0), (-2, -1, 0, 1),
                                   (2, 3, 1, 2), (3, 2, 2, 1), (1, 3, 1, 1), (3, -1, 1, 0),
                                   (-1, 2, 1, 2), (2, -3, 0, 1), (-2, 1, 1, 1), (1, -2, 2, 1),
                                   (-3, 1, 0, 1), (3, -3, 1, 1), (-1, 3, 2, 1), (3, 1, 1, 2),
                                   (-3, 2, 1, 0), (1, 2, 2, 0), (-2, -3, 1, 2), (2, 1, 1, 2)])
def test_grid_dsolve_crosscheck(mod, combo):
    """逐格：族断言全过 + sp.dsolve 独立求解与构造解等价 + 题干卫生。"""
    r1, r2, a, b = combo
    q = mod.ode2_distinct(r1, r2, a, b)
    assert q is not None, f"参数 {combo} 应合法"
    for name, ok in q["assert"]:
        assert bool(ok), f"{combo} 断言 {name} 未过"

    x = sp.Symbol("x")
    y = sp.Function("y")
    pv, qv = q["params"]["p"], q["params"]["q"]
    sol = sp.dsolve(y(x).diff(x, 2) + pv * y(x).diff(x) + qv * y(x), y(x),
                    ics={y(0): a, y(x).diff(x).subs(x, 0): b})
    answer = sp.sympify(q["answer_sympy"])
    assert sp.simplify(sol.rhs - answer) == 0, f"{combo} dsolve 交叉验证不一致"

    s = q["statement_md"]
    assert s and "初值" in s and "特解" in s and "______" in s
    assert f"y(0) = {a}" in s
    for banned in ("201", "202", "真题", "考研题"):
        assert banned not in s


def test_illegal_params_rejected(mod):
    """重根/零根/零初值必须被拒绝（相异非零根前提）。"""
    assert mod.ode2_distinct(1, 1, 1, 0) is None      # 重根
    assert mod.ode2_distinct(0, 2, 1, 0) is None      # 零根
    assert mod.ode2_distinct(1, 2, 0, 0) is None      # 零初值（零解平凡）


def test_answer_diversity(mod):
    items = mod.enumerate_family(limit=24)
    answers = {it["answer_sympy"] for it in items}
    assert len(answers) >= 12, "答案多样性过低（防背答案）"
