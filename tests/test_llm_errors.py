"""llm.chat 错误分类单测（模块 A：Fatal 立即失败 vs Retryable 重试耗尽）。

全部不出网：client 用 monkeypatch 替身（鸭子类型仿 openai 异常，不 import SDK 异常类），
缓存目录指到 tmp_path，time.sleep 打桩。零真实 key 依赖。
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm  # noqa: E402


# ---- 替身设施 ----
class FakeStatusError(Exception):
    """仿 openai APIStatusError：带 status_code 属性（鸭子类型，不 import SDK）。"""

    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message)
        self.status_code = status_code


class FakeClient:
    """仿 OpenAI client：chat.completions.create 按脚本逐次返回/抛出。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls += 1
        if self.calls > len(self._script):
            raise AssertionError("client 调用次数超出脚本（不应发生）")
        item = self._script[self.calls - 1]
        if isinstance(item, Exception):
            raise item
        return item


def _resp(text="ok"):
    """仿成功响应：resp.choices[0].message.content == text。"""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _empty_resp():
    """仿「调用成功但结构异常」：HTTP 200 但 choices 为空。"""
    return SimpleNamespace(choices=[])


def install_client(monkeypatch, script) -> FakeClient:
    fake = FakeClient(script)
    monkeypatch.setattr(llm, "_get_client", lambda: fake)
    return fake


_SLEEPS: list[float] = []


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch, tmp_path):
    """测试隔离：清 DEMO 环境变量、缓存落 tmp、sleep 打桩（避免真实退避等待）。

    缓存目录打桩用 MATHFORGE_CACHE_DIR 环境变量（模块 B 后 _cache_path 每次调用
    优先读它），而非 CACHE_DIR 模块属性——conftest 的 per-test 密封目录在此被
    覆盖为本测试的 tmp_path/llm_cache，断言缓存文件位置才有确定锚点。
    """
    monkeypatch.delenv("MATHFORGE_DEMO", raising=False)
    monkeypatch.setenv("MATHFORGE_CACHE_DIR", str(tmp_path / "llm_cache"))
    _SLEEPS.clear()
    monkeypatch.setattr(llm.time, "sleep", _SLEEPS.append)


# ---- 异常类兼容性 ----
def test_error_classes_inherit_runtime_error():
    # 兼容保证：现有调用方 except Exception / except RuntimeError 的宽捕获行为不变
    assert issubclass(llm.LLMFatalError, RuntimeError)
    assert issubclass(llm.LLMRetryableError, RuntimeError)


# ---- Fatal：立即失败，0 次重试 ----
def test_401_fatal_no_retry(monkeypatch):
    fake = install_client(monkeypatch, [FakeStatusError(401, "Invalid API key")])
    with pytest.raises(llm.LLMFatalError) as ei:
        llm.chat([{"role": "user", "content": "hi"}], namespace="t401")
    assert fake.calls == 1  # 认证失败重试无意义：只调 1 次
    assert _SLEEPS == []  # 0 次退避
    assert "认证失败" in str(ei.value)
    assert "DEEPSEEK_API_KEY" in str(ei.value)  # 人话+具体：指到 .env
    assert ei.value.__cause__ is not None  # 保留原始异常链


def test_400_fatal_no_retry(monkeypatch):
    fake = install_client(monkeypatch, [FakeStatusError(400, "Invalid request: temperature")])
    with pytest.raises(llm.LLMFatalError) as ei:
        llm.chat([{"role": "user", "content": "hi"}], namespace="t400")
    assert fake.calls == 1
    assert _SLEEPS == []
    assert "参数错误" in str(ei.value)


def test_404_model_not_found_fatal(monkeypatch):
    fake = install_client(monkeypatch, [FakeStatusError(404, "Model Not Exists")])
    with pytest.raises(llm.LLMFatalError) as ei:
        llm.chat([{"role": "user", "content": "hi"}], model="deepseek-nonexistent",
                 namespace="t404")
    assert fake.calls == 1
    assert _SLEEPS == []
    assert "模型不存在" in str(ei.value)


def test_auth_keyword_without_status_code_is_fatal(monkeypatch):
    # 兜底路径：无 status_code 的异常按关键词判（大小写不敏感）
    fake = install_client(monkeypatch, [Exception("Invalid API key provided")])
    with pytest.raises(llm.LLMFatalError) as ei:
        llm.chat([{"role": "user", "content": "hi"}], namespace="tkw")
    assert fake.calls == 1
    assert _SLEEPS == []
    assert "Invalid API key" in str(ei.value)


# ---- Retryable：重试 3 次耗尽 ----
def test_429_retries_three_times_then_retryable(monkeypatch, tmp_path):
    fake = install_client(monkeypatch, [FakeStatusError(429, "rate limit reached")] * 3)
    with pytest.raises(llm.LLMRetryableError) as ei:
        llm.chat([{"role": "user", "content": "hi"}], namespace="t429")
    assert fake.calls == 3
    assert _SLEEPS == [2, 4, 6]  # 沿用原有退避节奏 2*(attempt+1)
    assert "rate limit reached" in str(ei.value)  # 附最后一次错误摘要
    assert list((tmp_path / "llm_cache").glob("*.json")) == []  # 失败路径不写缓存


def test_5xx_retries_then_retryable(monkeypatch):
    fake = install_client(monkeypatch, [FakeStatusError(503, "service overloaded")] * 3)
    with pytest.raises(llm.LLMRetryableError):
        llm.chat([{"role": "user", "content": "hi"}], namespace="t5xx")
    assert fake.calls == 3
    assert _SLEEPS == [2, 4, 6]


def test_timeout_is_retryable(monkeypatch):
    # 兜底路径：超时类（无 status_code）→ Retryable
    fake = install_client(monkeypatch, [TimeoutError("Request timed out.")] * 3)
    with pytest.raises(llm.LLMRetryableError):
        llm.chat([{"role": "user", "content": "hi"}], namespace="ttimeout")
    assert fake.calls == 3


def test_connection_error_is_retryable(monkeypatch):
    # 兜底路径：网络连接错误（无 status_code）→ Retryable
    fake = install_client(monkeypatch, [ConnectionError("Connection error.")] * 3)
    with pytest.raises(llm.LLMRetryableError):
        llm.chat([{"role": "user", "content": "hi"}], namespace="tconn")
    assert fake.calls == 3


def test_unknown_exception_defaults_to_retryable(monkeypatch):
    # 普通异常（KeyError 等，根因不明）→ 保守归 Retryable，理由见 llm._classify_error 注释
    fake = install_client(monkeypatch, [KeyError("choices")] * 3)
    with pytest.raises(llm.LLMRetryableError):
        llm.chat([{"role": "user", "content": "hi"}], namespace="tunk")
    assert fake.calls == 3


def test_empty_choices_structural_is_retryable(monkeypatch):
    # 「调用成功但结构异常」（200 但 choices 空）：单独归 Retryable——请求被接受，重试有意义
    fake = install_client(monkeypatch, [_empty_resp()] * 3)
    with pytest.raises(llm.LLMRetryableError) as ei:
        llm.chat([{"role": "user", "content": "hi"}], namespace="tempty")
    assert fake.calls == 3
    assert "choices" in str(ei.value)


# ---- 重试后成功 + 缓存 ----
def test_two_429_then_success_returns_and_caches(monkeypatch, tmp_path):
    fake = install_client(monkeypatch, [FakeStatusError(429, "rate limit"),
                                        FakeStatusError(429, "rate limit"),
                                        _resp(" recovered ")])
    out = llm.chat([{"role": "user", "content": "hi"}], namespace="tmixed")
    assert out == " recovered "
    assert fake.calls == 3  # 前 2 次限流重试，第 3 次成功
    assert _SLEEPS == [2, 4]
    files = list((tmp_path / "llm_cache").glob("*.json"))  # 缓存写到打桩后的 tmp 目录
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["text"] == " recovered "


def test_cache_hit_does_not_touch_client(monkeypatch):
    install_client(monkeypatch, [_resp("cached text")])
    assert llm.chat([{"role": "user", "content": "hi"}], namespace="thit") == "cached text"
    # 换上「一被调用就炸」的哨兵 client：缓存命中必须完全不触发 client
    sentinel = install_client(monkeypatch, [AssertionError("缓存命中不应触发 client")])
    assert llm.chat([{"role": "user", "content": "hi"}], namespace="thit") == "cached text"
    assert sentinel.calls == 0


# ---- chat_json 对 Fatal 的传播（逻辑不变，只验证传播）----
def test_chat_json_fatal_propagates(monkeypatch):
    fake = install_client(monkeypatch, [FakeStatusError(401, "Invalid API key")])
    with pytest.raises(llm.LLMFatalError):
        llm.chat_json([{"role": "user", "content": "hi"}], namespace="tjson")
    assert fake.calls == 1  # chat_json 不吞 Fatal、不额外重试


# ---- P4b：重试中途遇 Fatal，提前终止 ----
def test_fatal_mid_retry_aborts(monkeypatch):
    # 第 1 次 429（可重试）→ 第 2 次 401（致命）：立即终止，不再第 3 次
    fake = install_client(monkeypatch, [FakeStatusError(429, "rate limit"),
                                        FakeStatusError(401, "Invalid API key")])
    with pytest.raises(llm.LLMFatalError) as ei:
        llm.chat([{"role": "user", "content": "hi"}], namespace="tmid")
    assert fake.calls == 2
    assert _SLEEPS == [2]  # 只有第一次 429 退避过一次
    assert "认证失败" in str(ei.value)


# ---- P4c：_classify_error 直测（分类稳健性）----
def test_classify_string_status_code_falls_to_keywords():
    # status_code 非数值（如字符串 "429"）→ 必须落关键词兜底，不误判、不崩溃
    assert llm._classify_error(FakeStatusError("429", "rate limit reached"))[0] \
        is llm.LLMRetryableError
    assert llm._classify_error(FakeStatusError("401", "authentication failed"))[0] \
        is llm.LLMFatalError


def test_classify_none_or_empty_message_no_crash():
    # 消息为 None/空的普通异常：归 Retryable 且不崩
    assert llm._classify_error(Exception(None))[0] is llm.LLMRetryableError
    assert llm._classify_error(Exception(""))[0] is llm.LLMRetryableError


# ---- P1：key 未设置属致命错，不重试 ----
MSG_NO_KEY = "DEEPSEEK_API_KEY 未设置：请确认 mathforge/.env 存在且含 key"


def test_missing_key_is_fatal_no_retry(monkeypatch):
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(llm.LLMFatalError) as ei:
        llm._get_client()
    assert str(ei.value) == MSG_NO_KEY  # 原人话文案
    # 经 chat 循环：Fatal 立即向上传播，消息原样保留、0 次重试
    with pytest.raises(llm.LLMFatalError) as ei2:
        llm.chat([{"role": "user", "content": "hi"}], namespace="tnokey")
    assert str(ei2.value) == MSG_NO_KEY
    assert _SLEEPS == []


# ---- P3：demo 红线（只读缓存，不静默降级）----
def test_demo_cache_miss_raises_no_client(monkeypatch):
    sentinel = install_client(monkeypatch, [AssertionError("demo 模式不应触发 client")])
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    with pytest.raises(RuntimeError) as ei:
        llm.chat([{"role": "user", "content": "hi"}], namespace="tdemomiss")
    assert type(ei.value) is RuntimeError  # 红线原样：普通 RuntimeError，未静默降级
    assert "演示数据需预热" in str(ei.value)
    assert sentinel.calls == 0  # demo 未命中绝不触网


def test_demo_cache_hit_returns_without_client(monkeypatch):
    install_client(monkeypatch, [_resp("demo cached")])
    assert llm.chat([{"role": "user", "content": "hi"}], namespace="tdemohit") == "demo cached"
    sentinel = install_client(monkeypatch, [AssertionError("demo 缓存命中不应触发 client")])
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    assert llm.chat([{"role": "user", "content": "hi"}], namespace="tdemohit") == "demo cached"
    assert sentinel.calls == 0  # demo 命中只读缓存，零 client 调用


# ---- 模块 A 重试单层化：SDK 不做隐式重试，单次超时归本层分类重试接管 ----
def test_get_client_disables_sdk_retry_and_sets_timeout(monkeypatch):
    """OpenAI(...) 构造参数：max_retries=0（SDK 默认自身再重试 2 次，会与本层 chat
    的 3 次退避叠加，宕机时单请求降级实测 68 秒）+ timeout=60.0（单次超时）。

    SDK 超时异常无 status_code、消息含 "timed out"，经 _classify_error 的关键词兜底
    落 Retryable 分支（test_timeout_is_retryable 已走全链路验证）——重试策略单层化，
    全部发生在 llm.chat 循环内。

    构造参数无法直接断言：monkeypatch 捕获 kwargs。注意 llm.py 是 `from openai import
    OpenAI`——运行期绑定在 llm 命名空间，必须 patch llm.OpenAI 才拦得住；且 _get_client
    有模块级 _client 单例缓存，先置 None 重置、迫使真构造（monkeypatch 测后自动还原）。
    """
    captured: dict = {}

    def _fake_openai(**kwargs):
        captured.update(kwargs)
        return object()  # 本用例只关心构造参数，调用行为由 FakeClient 用例负责

    monkeypatch.setattr(llm, "_client", None)  # 重置单例缓存
    monkeypatch.setattr(llm, "OpenAI", _fake_openai)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-retry-single-layer")

    client = llm._get_client()

    assert client is not None
    assert captured.get("max_retries") == 0   # SDK 隐式重试关闭
    assert captured.get("timeout") == 60.0    # 单次 60 秒超时
    assert captured.get("api_key") == "sk-retry-single-layer"
    assert captured.get("base_url") == llm.BASE_URL


def test_classify_sdk_timeout_message_is_retryable():
    """确认链路：SDK 单次超时异常（无 status_code，消息 "Request timed out."）落
    Retryable 分支——timeout=60.0 触发的超时由本层重试接管而非直接失败。"""
    assert llm._classify_error(TimeoutError("Request timed out."))[0] \
        is llm.LLMRetryableError
