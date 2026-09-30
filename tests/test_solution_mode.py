"""Day6 任务 2：解答题自评对照模式单测。

- 生成引擎解答题（qtype="solution"）：复用绿标验证链（SymPy 构造 + 盲解对账），
  家族路由 kp 零 API 可出（DEMO=1 下盲解失败也不阻断，走 judge_flag 留档）。
- v1 退役（2026-09-30）：/bank/self-assess、/bank/fix-attribution 端点随 v1 移除，
  对应端点用例删除；生成引擎用例保留（generate 链路不变）。
"""

import os
import sys
from pathlib import Path

os.environ["MATHFORGE_DEMO"] = "1"  # 盲解走缓存，未命中按不一致处理（judge_flag），不出网

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from generate import generate_question  # noqa: E402


@pytest.fixture(autouse=True)
def _fake_analysis_llm(monkeypatch):
    """模块 B 假依赖注入：解析实例化（generate._instantiate_analysis → chat_json）
    此前依赖仓库 cache/llm 的预热命中；密封后缓存指向 per-test 临时目录必然未命中，
    兜底模板解析为空 → analysis 断言失败。改打固定 JSON 替身，本组用例不再依赖真实缓存。
    """
    monkeypatch.setattr("generate.chat_json",
                        lambda *a, **kw: {"analysis": "先求导得 f'(x)=a(x-ξ)...，"
                                                   "由 f(p)=f(q)=0 与罗尔定理知存在 ξ 使 f'(ξ)=0。"})


def test_generated_solution_question_uses_green_chain():
    q = generate_question({"kp": "罗尔定理"}, difficulty="基础", qtype="solution")
    assert q.get("statement_md"), q.get("error")
    assert q["qtype"] == "solution"
    assert q["verify_level"] == "green"          # 验证链与计算题一致
    assert q.get("answer_sympy")                 # SymPy 推导的标准答案
    assert q.get("analysis")


def test_calculation_qtype_unchanged():
    q = generate_question({"kp": "拉格朗日中值定理"}, difficulty="基础", qtype="calculation")
    assert q.get("statement_md"), q.get("error")
    assert q["qtype"] == "calculation"
