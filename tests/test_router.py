"""router 两段式路由单测。

覆盖：3 例 kp_id→pack（calc.rolle/la.eigen/prob.expectation）/
2 例自由文本（矩阵的特征值 → linear；期望 → probability）/
DEMO 降级路径零 API（多候选 → stage2-fallback，绝不崩）。
不触网；DEMO 通过 monkeypatch 环境变量保证零 API。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import router


def test_route_kp_calculus():
    r = router.route({"kp_id": "calc.rolle"})
    assert r["pack_id"] == "calculus"
    assert r["path"] == "stage1"
    assert r["examiner_prompt"]
    assert r["confidence"] == 1.0


def test_route_kp_linear():
    r = router.route({"kp_id": "la.eigen"})
    assert r["pack_id"] == "linear"
    assert r["path"] == "stage1"


def test_route_kp_probability():
    r = router.route({"kp_id": "prob.expectation"})
    assert r["pack_id"] == "probability"
    assert r["path"] == "stage1"


def test_route_free_text_eigen():
    r = router.route({"query": "矩阵的特征值"})
    assert r["pack_id"] == "linear"
    assert r["path"] == "stage1"


def test_route_free_text_expectation():
    r = router.route({"query": "期望与方差怎么求"})
    assert r["pack_id"] == "probability"
    assert r["path"] == "stage1"


def test_route_unrouted_empty():
    r = router.route({"query": "zzz无匹配 xyz"})
    assert r["path"] == "unrouted"
    assert r["pack_id"] is None
    assert r["confidence"] == 0.0


def test_route_demo_fallback_zero_api(monkeypatch):
    # 多候选自由文本 + DEMO → stage2-fallback 取首位，不触网不崩
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert router._demo_mode() is True
    r = router.route({"query": "计算"})  # 命中多个 kp
    assert r["path"] == "stage2-fallback"
    assert r["pack_id"] is not None
    assert r["confidence"] == 0.5


def test_build_index_flattened():
    idx = router.build_index()
    assert isinstance(idx, list) and len(idx) > 0
    ids = {e["kp_id"] for e in idx}
    assert "calc.rolle" in ids and "la.eigen" in ids and "prob.expectation" in ids
