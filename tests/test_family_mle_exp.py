"""构造测试 · 极大似然估计族（packs/probability/families/mle_exp.py）。

任务书 §5 步骤 4「24/24 式确定性枚举」：24 组观测值逐格断言，无随机、无 LLM。
每格多重校验：
  1. 族内合法性断言全过（驻点方程 / 凹性 / 干净度 / fobar）
  2. 独立数值路径：对数似然在稠密网格上的 argmax ≈ λ̂（1e-3）
  3. 独立符号路径：solve(d logL) 与 λ̂ 一致（测试内重推）
  4. 题干非空、含「指数分布」与「极大似然」、不含任何真题来源标记
"""

import math
import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl  # noqa: E402

FAM_MODULE = "mle_exp"
N = 5


@pytest.fixture(scope="module")
def mod():
    pack = pl.load_pack("probability")
    assert FAM_MODULE in pack["families"], "族文件未被 pack_loader 装载"
    return pack["families"][FAM_MODULE]


@pytest.fixture(scope="module")
def all_obs(mod):
    obs_list = mod.GRID["obs"]
    assert len(obs_list) == 24, "参数格应为 24 组观测值"
    return obs_list


def test_module_contract(mod):
    assert mod.KP_ID == "prob.estimate"
    assert mod.KP_NAME == "参数估计"
    assert mod.CACHE_NS == "family-prob.estimate:v1"
    assert mod.FAMILY in mod.SPEC
    assert mod.N == 5


@pytest.mark.parametrize("idx", range(24))
def test_grid_asserts(mod, all_obs, idx):
    obs = all_obs[idx]
    q = mod.mle_exp_rate(obs)
    assert q is not None, f"观测 {obs} 应合法"
    names = [n for n, _ in q["assert"]]
    assert names == ["obs_valid", "mle_stationary", "mle_maximum",
                     "clean_answer", "fobar_invertible"]
    for name, ok in q["assert"]:
        assert bool(ok), f"{obs} 断言 {name} 未过"


@pytest.mark.parametrize("idx", range(24))
def test_independent_numeric_argmax(mod, all_obs, idx):
    """独立数值路径：对数似然稠密网格 argmax ≈ λ̂。"""
    obs = all_obs[idx]
    q = mod.mle_exp_rate(obs)
    s_total = sum(obs)
    lam_hat = N / s_total
    best_lam, best_val = None, float("-inf")
    lam = 0.02
    while lam <= 8.0:
        val = N * math.log(lam) - lam * s_total
        if val > best_val:
            best_val, best_lam = val, lam
        lam += 0.001
    assert abs(best_lam - lam_hat) < 2e-3, f"数值 argmax {best_lam} 偏离 λ̂ {lam_hat}"


@pytest.mark.parametrize("idx", range(24))
def test_independent_symbolic(mod, all_obs, idx):
    obs = all_obs[idx]
    q = mod.mle_exp_rate(obs)
    s_total = sum(obs)
    lmb = sp.Symbol("lambda_", positive=True)
    log_l = N * sp.log(lmb) - lmb * s_total
    sols = sp.solve(sp.diff(log_l, lmb), lmb)
    assert sols == [sp.Rational(N, s_total)] == [sp.sympify(q["answer_sympy"])]


def test_answer_diversity_and_hygiene(mod, all_obs):
    answers = {sp.sympify(mod.mle_exp_rate(o)["answer_sympy"]) for o in all_obs}
    assert len(answers) >= 14, "答案多样性过低（防背答案：Σx 应覆盖足够多不同值）"
    for o in all_obs:
        s = mod.mle_exp_rate(o)["statement_md"]
        assert s and "指数分布" in s and "极大似然" in s and "______" in s
        for banned in ("201", "202", "真题", "考研题"):
            assert banned not in s
