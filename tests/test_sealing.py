"""模块 B 密封性自检：conftest 的 autouse 环境密封必须对全部测试生效。

验证五件事（干净检出、无 cache/llm 预热缓存、无 .env 时同样成立）：
1. 测试运行中 MATHFORGE_DEMO == "1"（演示模式红线：只读缓存、未命中即报错，绝不出网）；
2. 真实 DEEPSEEK_API_KEY 不泄漏进测试进程环境（.env 经 load_dotenv 注入也会被清掉）；
3. MATHFORGE_CACHE_DIR 指向 per-test 临时目录，绝不指向仓库真实 cache/llm，且逐测试唯一；
4. llm._cache_path() 尊重 MATHFORGE_CACHE_DIR：缓存读写落在密封目录，仓库缓存零接触；
5. 残留 MATHFORGE_* 运行期变量（.env 灌入/测试间泄漏）被 conftest 全量清扫（豁免集除外）。
"""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llm  # noqa: E402
try:  # pytest 以包内模块加载仓库根 conftest（根目录有 __init__.py）
    from mathforge import conftest as _conftest
except ImportError:  # 兜底：顶层加载形态
    import conftest as _conftest

_REPO_CACHE = ROOT / "cache" / "llm"

# 模块级 fixture 直设、清扫必须豁免的测试隔离变量（与 conftest._SWEEP_EXEMPT 同步）
_SWEEP_EXEMPT = frozenset({"MATHFORGE_V2_DB", "MATHFORGE_PROFILES_DIR"})


def _run_sealed_fixture(monkeypatch, tmp_dir):
    """直接执行 conftest 密封 fixture 的裸函数（pytest 9 禁止直呼 fixture，
    其包装器把原函数挂在 __wrapped__；取不到时退化为复现逻辑，与
    conftest._sealed_test_env 保持同步）。供单测试内自洽验证用。"""
    fn = getattr(_conftest._sealed_test_env, "__wrapped__", None)
    if fn is not None:
        fn(monkeypatch, tmp_dir)
        return
    for k in [k for k in os.environ
              if k.startswith("MATHFORGE_") and k not in _SWEEP_EXEMPT]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MATHFORGE_CACHE_DIR", str(Path(tmp_dir) / "llm_cache"))
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def test_demo_mode_forced_by_conftest():
    """红线：测试进程内 MATHFORGE_DEMO 恒为 1（缓存未命中报错语义不变）。"""
    assert os.environ.get("MATHFORGE_DEMO") == "1"


def test_real_api_key_absent_in_test_env():
    """真实 key 不得泄漏进测试行为：v2/router 等按此变量判断在线态，测试内一律无 key。"""
    assert "DEEPSEEK_API_KEY" not in os.environ


def test_cache_dir_sealed_into_per_test_tmp(tmp_path):
    sealed = os.environ.get("MATHFORGE_CACHE_DIR")
    assert sealed, "MATHFORGE_CACHE_DIR 未设置：conftest autouse 密封 fixture 未生效"
    p = Path(sealed)
    assert p.is_absolute()
    assert p != _REPO_CACHE and _REPO_CACHE not in p.parents  # 绝不指向/落入仓库真实缓存
    assert str(tmp_path) in sealed                            # per-test 临时目录内


def test_cache_dir_is_per_test_unique(tmp_path, tmp_path_factory):
    """per-test 唯一性（单测试内自洽，无跨测试状态、不依赖执行顺序）：
    用第二个独立 tmp 目录 + 全新 MonkeyPatch 再执行一次 conftest 密封 fixture，
    断言两次所得密封目录不同、各自指向自己的 tmp，且撤销后本测试环境不被污染。"""
    sealed_now = Path(os.environ["MATHFORGE_CACHE_DIR"])
    other = tmp_path_factory.mktemp("sealing_uniqueness")
    mp = pytest.MonkeyPatch()
    try:
        _run_sealed_fixture(mp, other)
        sealed_other = Path(os.environ["MATHFORGE_CACHE_DIR"])
        assert sealed_other == other / "llm_cache"
        assert sealed_other != sealed_now
    finally:
        mp.undo()
    assert Path(os.environ["MATHFORGE_CACHE_DIR"]) == sealed_now  # 撤销即还原


def test_conftest_sweeps_stray_mathforge_env(tmp_path):
    """清扫自检：残留 MATHFORGE_* 变量（.env 灌入/其他测试泄漏）在 fixture 执行后
    不得残留；豁免集 MATHFORGE_V2_DB / MATHFORGE_PROFILES_DIR 不扫——它们由外层
    模块级 fixture 直设，清扫会破坏其作用域语义（v2 请求会回落到真实库）。"""
    leaked = "MATHFORGE_TEST_LEAK_PROBE"
    mp = pytest.MonkeyPatch()
    try:
        # 泄漏变量：清扫后消失；密封值在清扫之后再落
        mp.setenv(leaked, "still-here")
        mp.delenv("MATHFORGE_CACHE_DIR", raising=False)
        mp.delenv("MATHFORGE_DEMO", raising=False)
        _run_sealed_fixture(mp, tmp_path)
        assert leaked not in os.environ
        assert os.environ["MATHFORGE_DEMO"] == "1"
        assert os.environ["MATHFORGE_CACHE_DIR"] == str(tmp_path / "llm_cache")
        # 豁免变量：模块级 fixture 直设的隔离变量得以存活
        mp.setenv("MATHFORGE_V2_DB", "exempt-check")
        mp.setenv("MATHFORGE_PROFILES_DIR", "exempt-check")
        _run_sealed_fixture(mp, tmp_path)
        assert os.environ.get("MATHFORGE_V2_DB") == "exempt-check"
        assert os.environ.get("MATHFORGE_PROFILES_DIR") == "exempt-check"
    finally:
        mp.undo()


def test_llm_cache_path_respects_env_var(tmp_path):
    """llm._cache_path() 每次调用读 MATHFORGE_CACHE_DIR（无 lru_cache 状态泄漏）。"""
    sealed = Path(os.environ["MATHFORGE_CACHE_DIR"])
    cp = llm._cache_path("sealing_probe")
    assert cp.parent == sealed
    assert cp.name == "sealing_probe.json"
    # 写入落密封目录，仓库真实缓存零接触
    cp.write_text(json.dumps({"text": "sealed", "model": "x", "ts": 0.0}), encoding="utf-8")
    assert (sealed / "sealing_probe.json").exists()
    assert not (_REPO_CACHE / "sealing_probe.json").exists()


def test_chat_reads_sealed_cache_demo_hit(tmp_path):
    """端到端：chat 在密封目录命中即返回缓存文本（DEMO=1 下命中不出网、不触仓库缓存）。"""
    sealed = Path(os.environ["MATHFORGE_CACHE_DIR"])
    sealed.mkdir(parents=True, exist_ok=True)  # chat 首次调用才建目录，此处预置缓存文件
    payload = {"m": [{"role": "user", "content": "sealing probe"}],
               "model": llm.DEFAULT_MODEL, "t": 1.0, "j": False}
    key = hashlib.sha256(("sealing_ns" + json.dumps(payload, ensure_ascii=False)).encode()) \
        .hexdigest()[:40]
    (sealed / f"{key}.json").write_text(
        json.dumps({"text": "sealed hit", "model": llm.DEFAULT_MODEL, "ts": 0.0},
                   ensure_ascii=False), encoding="utf-8")
    assert llm.chat([{"role": "user", "content": "sealing probe"}],
                    namespace="sealing_ns") == "sealed hit"
    assert not (_REPO_CACHE / f"{key}.json").exists()  # 仓库缓存零接触
