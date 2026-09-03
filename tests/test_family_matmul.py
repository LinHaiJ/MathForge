"""构造测试 · 矩阵乘法指定元族（packs/linear/families/matmul.py，D8-6 包内化）。

委托 v1 repo 根 families 的 matmul_entry 族完成枚举。校验项同 rolle。
与 inverse2x2 同属 KP_ID=la.matrix.basic（v2api 按 KP_ID 命中，两模块互补）。
"""

import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM_MODULE = "matmul"
PACK_ID = "linear"
KP_ID = "la.matrix.basic"
KP_NAME = "矩阵乘法"
CACHE_NS = "family-la.matrix.basic:v1"
FAMILY = "matmul_entry"
EXPECTED = 24
MATRIX_ANS = False


def _parse(s):
    return sp.sympify(s, locals={"Matrix": sp.Matrix}) if MATRIX_ANS else sp.sympify(s)


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack(PACK_ID)
    assert FAM_MODULE in pack["families"], "族文件未被 pack_loader 装载"
    return pack["families"][FAM_MODULE]


def test_module_contract(mod):
    assert mod.KP_ID == KP_ID
    assert mod.KP_NAME == KP_NAME
    assert mod.CACHE_NS == CACHE_NS
    assert mod.FAMILY == FAMILY
    assert mod.FAMILY in mod.SPEC


def test_kp_in_pack_graph():
    pack = pl.load_pack(PACK_ID)
    kp = pl.get_kp(pack, KP_ID)
    assert kp is not None and kp.get("verifiable") is True


def test_enumerate_full_grid(mod):
    items = mod.enumerate_family(limit=24)
    assert len(items) == EXPECTED, f"期望 {EXPECTED} 题，实得 {len(items)}"

    keys = {tuple(sorted(it["params"].items())) for it in items}
    assert len(keys) == len(items), "参数组合非互异"
    if EXPECTED >= 8:
        assert len(keys) > 8, "参数互异应 >8 组"

    for it in items:
        for f in ("kp_id", "family", "params", "statement_md",
                  "answer_sympy", "difficulty", "assert", "verify_level"):
            assert f in it, f"缺字段 {f}"
        assert it["kp_id"] == KP_ID
        assert it["family"] == FAMILY
        assert it["statement_md"].strip()
        assert it["verify_level"] == "green"
        assert "degenerate" not in it, "出现退化标记"

        fails = [n for n, ok in it["assert"] if not bool(ok)]
        assert not fails, f"断言失败 {fails}"

        ans = _parse(it["answer_sympy"])
        simp = sp.simplify(ans)
        assert simp is not None


def test_deterministic_repeat(mod):
    a = mod.enumerate_family(limit=24)
    b = mod.enumerate_family(limit=24)
    assert [i["answer_sympy"] for i in a] == [i["answer_sympy"] for i in b]
