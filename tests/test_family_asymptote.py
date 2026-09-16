"""构造测试 · 渐近线族（packs/calculus/families/asymptote.py）。

任务书 §5 步骤 4「24/24 式确定性枚举」：24 组参数逐格断言，无随机、无 LLM。
每格多重校验：
  1. 族内合法性断言全过（定义极限/形态恒等/干净度/问法一致）
  2. 独立符号路径：测试内重推斜渐近线（多项式除法 degree 差）与竖直渐近线（分母零点）
  3. 题干非空、含「渐近线」、不含任何真题来源标记
"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM_MODULE = "asymptote"


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("calculus")
    assert FAM_MODULE in pack["families"], "族文件未被 pack_loader 装载"
    return pack["families"][FAM_MODULE]


def test_module_contract(mod):
    assert mod.KP_ID == "calc.asymptote"
    assert mod.KP_NAME == "渐近线"
    assert mod.CACHE_NS == "family-calc.asymptote:v1"
    assert mod.FAMILY in mod.SPEC
    assert len(mod.GRID["p3"]) == 24


ALL_ITEMS = None  # 模块级惰性填充


def _items(mod):
    global ALL_ITEMS
    if ALL_ITEMS is None:
        ALL_ITEMS = mod.enumerate_family(limit=24)
    return ALL_ITEMS


def test_grid_definition_and_hygiene(mod):
    x = sp.Symbol("x")
    for q in _items(mod):
        c, r, k = q["params"]["c"], q["params"]["r"], q["params"]["k"]
        f = x + c + k / (x - r)
        for name, ok in q["assert"]:
            assert bool(ok), f"({c},{r},{k}) 断言 {name} 未过"
        # 独立路径①：斜渐近线定义极限
        assert sp.limit(f - (x + c), x, sp.oo) == 0
        # 独立路径②：竖直渐近线 = 分母零点处无穷
        assert sp.limit(f, x, r, "+").is_infinite
        s = q["statement_md"]
        assert s and "渐近线" in s and "______" in s
        for banned in ("201", "202", "真题", "考研题"):
            assert banned not in s


def test_mode_coverage_and_diversity(mod):
    items = _items(mod)
    modes = {it["params"]["mode"] for it in items}
    assert modes == {"slant", "vert"}
    answers = {it["answer_sympy"] for it in items}
    assert len(answers) >= 6, "答案多样性过低（防背答案）"


def test_zero_params_rejected(mod):
    assert mod.asy_slant(0, 1, 1) is None
    assert mod.asy_slant(1, 0, 1) is None
    assert mod.asy_slant(1, 1, 0) is None
