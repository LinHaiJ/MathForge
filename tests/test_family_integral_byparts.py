"""构造测试 · 分部积分族（packs/calculus/families/integral_byparts.py）。

§5 步骤 4「24/24 式确定性枚举」：参数格 4×6=24 格逐格断言，无随机、无 LLM。
每格四重校验：
  1. 族内合法性断言全过（原函数求导自证 / 闭式对账 / 无奇点 / 干净度 / fobar）
  2. answer_sympy 与 SymPy 独立符号积分路径一致（族用原函数，测试用 integrate）
  3. answer_sympy 与 mpmath 数值积分一致（第三条独立路径，1e-9）
  4. 题干非空、含积分号与区间、不含任何真题来源标记
"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM_MODULE = "integral_byparts"
GRID_COMBOS = [(a, k) for a in [1, 2, 3, 4] for k in [1, 2, 3, 4, 5, 6]]
assert len(GRID_COMBOS) == 24


@pytest.fixture(scope="module")
def mod():
    """经 pack_loader 动态加载（顺带验证 pack 契约装载路径可用）。"""
    pack = pl.load_pack("calculus")
    assert FAM_MODULE in pack["families"], "族文件未被 pack_loader 装载"
    return pack["families"][FAM_MODULE]


def test_module_contract(mod):
    assert mod.KP_ID == "calc.integral.byparts"
    assert mod.KP_NAME == "分部积分"
    assert mod.CACHE_NS == "family-calc.integral.byparts:v1"
    assert mod.FAMILY in mod.SPEC
    assert mod.GRID == {"a": [1, 2, 3, 4], "k": [1, 2, 3, 4, 5, 6]}


def test_kp_in_pack_graph():
    pack = pl.load_pack("calculus")
    kp = pl.get_kp(pack, "calc.integral.byparts")
    assert kp is not None and kp["verifiable"] is True


@pytest.mark.parametrize("a,k", GRID_COMBOS)
def test_grid_instance(mod, a, k):
    q = mod.byparts_xsin(a, k)
    assert q is not None, f"参数格 (a={a},k={k}) 未产出实例"

    # 1. 族内断言全过
    fails = [name for name, ok in q["assert"] if not bool(ok)]
    assert not fails, f"(a={a},k={k}) 断言失败: {fails}"

    # 2. 独立符号路径：SymPy 直接定积分
    x = sp.Symbol("x")
    ref = sp.simplify(sp.integrate(a * x * sp.sin(k * x), (x, 0, sp.pi)))
    ans = sp.sympify(q["answer_sympy"])
    assert sp.simplify(ans - ref) == 0, f"(a={a},k={k}) 答案与独立积分不一致"

    # 3. 独立数值路径
    num = sp.N(sp.Integral(a * x * sp.sin(k * x), (x, 0, sp.pi)).evalf())
    assert abs(float(num) - float(sp.N(ans))) < 1e-9

    # 4. 题干纪律
    stem = q["statement_md"]
    assert stem and stem.strip()
    assert "\\int" in stem and "\\pi" in stem
    assert not any(t in stem for t in ("数一", "数二", "数三", "真题", "考研"))
    assert q["params"] == {"a": a, "k": k}
    assert q["kp"] == "分部积分" and q["family"] == "byparts_xsin"


def test_enumerate_full_grid(mod):
    items = mod.enumerate_family(limit=24)
    assert len(items) == 24, "24/24 未达成"
    # 参数组合唯一 + 答案确定性（同参数重复调用同答案）
    keys = {tuple(sorted(it["params"].items())) for it in items}
    assert len(keys) == 24
    again = mod.enumerate_family(limit=24)
    assert [i["answer_sympy"] for i in items] == [i["answer_sympy"] for i in again]


def test_clean_rejects_dirty(mod):
    """干净度闸门反向用例：含 e^k 的分部积分答案必须被 _clean 拒绝。"""
    x = sp.Symbol("x")
    dirty = sp.simplify(sp.integrate(x * sp.exp(2 * x), (x, 0, 1)))  # 1/4 + e²/4
    assert mod._clean(dirty) is False
    assert mod._clean(sp.pi / 7) is True
    assert mod._clean(sp.Rational(1, 200)) is False  # 分母 3 位
