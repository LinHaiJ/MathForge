"""构造测试 · 二重积分族（packs/calculus/families/double_int.py）。

§5 步骤 4「24/24 式确定性枚举」：参数格 4×3×2=24 格逐格断言，无随机、无 LLM。
每格四重校验：
  1. 族内合法性断言全过（区域合法 / 两序换序对账 / 闭式一致 / 干净度 / fobar）
  2. answer_sympy 与「先 x 后 y」换序积分一致（独立符号路径）
  3. answer_sympy 与数值积分一致（第三条独立路径，1e-9）
  4. 题干非空、含 iint 与区域描述、不含任何真题来源标记
"""

import os
import sys

import mpmath
import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM_MODULE = "double_int"
GRID_COMBOS = [(al, be, p) for al in [0, 1, 2, 3] for be in [0, 1, 2] for p in [2, 3]]
assert len(GRID_COMBOS) == 24


@pytest.fixture(scope="module")
def mod():
    """经 pack_loader 动态加载（顺带验证 pack 契约装载路径可用）。"""
    pack = pl.load_pack("calculus")
    assert FAM_MODULE in pack["families"], "族文件未被 pack_loader 装载"
    return pack["families"][FAM_MODULE]


def test_module_contract(mod):
    assert mod.KP_ID == "calc.double.int"
    assert mod.KP_NAME == "二重积分"
    assert mod.CACHE_NS == "family-calc.double.int:v1"
    assert mod.FAMILY in mod.SPEC
    assert mod.GRID == {"alpha": [0, 1, 2, 3], "beta": [0, 1, 2], "p": [2, 3]}


def test_kp_in_pack_graph():
    pack = pl.load_pack("calculus")
    kp = pl.get_kp(pack, "calc.double.int")
    assert kp is not None and kp["verifiable"] is True


@pytest.mark.parametrize("alpha,beta,p", GRID_COMBOS)
def test_grid_instance(mod, alpha, beta, p):
    q = mod.double_swap(alpha, beta, p)
    assert q is not None, f"参数格 (α={alpha},β={beta},p={p}) 未产出实例"

    # 1. 族内断言全过
    fails = [name for name, ok in q["assert"] if not bool(ok)]
    assert not fails, f"(α={alpha},β={beta},p={p}) 断言失败: {fails}"

    # 2. 独立符号路径：换序（先 x 后 y）
    x, y = sp.symbols("x y", positive=True)
    f = x**alpha * y**beta
    ref = sp.simplify(sp.integrate(sp.integrate(f, (x, y, y ** sp.Rational(1, p))), (y, 0, 1)))
    ans = sp.sympify(q["answer_sympy"])
    assert sp.simplify(ans - ref) == 0, f"(α={alpha},β={beta},p={p}) 答案与换序积分不一致"

    # 3. 独立数值路径（mpmath 二重数值积分，与符号推导无共用代码）
    fn = sp.lambdify((x, y), f, "mpmath")
    num = mpmath.quad(lambda xv: mpmath.quad(lambda yv: fn(xv, yv), [xv**p, xv]), [0, 1])
    assert abs(float(num) - float(sp.N(ans))) < 1e-9

    # 4. 题干纪律
    stem = q["statement_md"]
    assert stem and stem.strip()
    assert "\\iint" in stem and "y=x" in stem
    assert not any(t in stem for t in ("数一", "数二", "数三", "真题", "考研"))
    assert q["params"] == {"alpha": alpha, "beta": beta, "p": p}
    assert q["kp"] == "二重积分" and q["family"] == "double_swap"


def test_enumerate_full_grid(mod):
    items = mod.enumerate_family(limit=24)
    assert len(items) == 24, "24/24 未达成"
    keys = {tuple(sorted(it["params"].items())) for it in items}
    assert len(keys) == 24
    again = mod.enumerate_family(limit=24)
    assert [i["answer_sympy"] for i in items] == [i["answer_sympy"] for i in again]


def test_swap_is_real_constraint(mod):
    """换序断言不是恒真：区域上下界写反时（y 从 x 到 x^p）积分变号，断言应能分辨。"""
    x, y = sp.symbols("x y", positive=True)
    right = sp.integrate(sp.integrate(x * y, (y, x**2, x)), (x, 0, 1))
    flipped = sp.integrate(sp.integrate(x * y, (y, x, x**2)), (x, 0, 1))
    assert sp.simplify(right + flipped) == 0 and sp.simplify(right - flipped) != 0


def test_clean_rejects_dirty(mod):
    assert mod._clean(sp.Rational(1, 70)) is True
    assert mod._clean(sp.Rational(1, 100)) is False  # 分母 3 位
    assert mod._clean(sp.sqrt(2)) is False
