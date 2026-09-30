"""LLM 公共客户端：DeepSeek chat API + 全量磁盘缓存。

- key 从 mathforge/.env 读取（gitignored），只发往 DeepSeek 官方端点
- 所有调用写 cache/llm/<hash>.json → 演示模式零 API 依赖（PRD 演示规格）
- MATHFORGE_DEMO=1 时只读缓存，缓存未命中直接报错（不静默降级）
- 错误分类：LLMFatalError（重试无意义，立即失败）vs LLMRetryableError（重试耗尽才抛）
  ——借鉴 Claude Code 的错误分类模式：致命错误说清原因立即失败，不浪费重试。
  两者都继承 RuntimeError，现有调用方 except Exception / except RuntimeError 的
  宽捕获行为不变。
- 重试单层化（模块 A）：OpenAI SDK 不做隐式重试（max_retries=0）、单次超时 60s
  （timeout=60.0）——SDK 默认自身再重试 2 次，会与本层 chat 的 3 次退避叠加
  （LLM 宕机时单请求降级实测 68 秒）。SDK 超时异常（消息含 "timed out"）经
  _classify_error 落 Retryable 分支，由本层分类重试统一接管。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE / ".env")

CACHE_DIR = _HERE / "cache" / "llm"
DEFAULT_MODEL = "deepseek-chat"
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

_client: OpenAI | None = None


class LLMFatalError(RuntimeError):
    """LLM 致命错误：重试无意义（认证失败/参数错误/模型不存在），立即失败并说清原因。"""


class LLMRetryableError(RuntimeError):
    """LLM 可重试错误（限流/5xx/超时/网络）：重试 3 次耗尽后才抛出（附最后错误摘要）。"""


# 关键词兜底匹配（大小写不敏感）：无 status_code 的异常按类名+消息判断。
# 不 import SDK 异常类、不依赖 SDK 类层次——用鸭子类型，SDK 升级不破坏。
_FATAL_KEYWORDS = ("invalid api key", "api key", "authentication", "unauthorized",
                   "invalid request", "model not found")
_RETRYABLE_KEYWORDS = ("rate limit", "ratelimit", "timeout", "timed out",
                       "connection", "overloaded", "temporarily", "server error")


def _brief(e: BaseException, limit: int = 200) -> str:
    """异常单行摘要（截断），用于拼进错误消息。"""
    s = " ".join(f"{type(e).__name__}: {e}".split())
    return s if len(s) <= limit else s[: limit] + "…"


def _classify_error(e: BaseException) -> tuple[type[RuntimeError], str]:
    """把调用链中的任意异常归类为 (异常类, 人话原因)。

    分类优先级：
    1. 本模块已分类的异常（如结构异常）原样沿用；
    2. status_code 数值——openai SDK 的 APIStatusError 系都有此属性，
       用 getattr 鸭子类型读取，不 import SDK 异常类；
    3. 类名+消息关键词兜底（小写化、下划线归一为空格后匹配）；
    4. 都不命中 → Retryable（理由见函数尾部注释）。
    """
    if isinstance(e, (LLMFatalError, LLMRetryableError)):
        # 本模块已分类的错误（如 key 未设置、结构异常）：沿用原类与原消息
        return type(e), str(e)
    code = getattr(e, "status_code", None)
    if isinstance(code, int):
        if code == 401:
            return LLMFatalError, "DeepSeek 认证失败（401）：请检查 mathforge/.env 中 DEEPSEEK_API_KEY"
        if code == 400:
            return LLMFatalError, f"DeepSeek 请求参数错误（400）：{_brief(e)}——请检查 model 名与参数取值"
        if code == 402:
            return LLMFatalError, f"DeepSeek 余额不足（402）：请充值或检查额度——{_brief(e)}"
        if code == 403:
            return LLMFatalError, f"DeepSeek 无权限（403）：请检查 API key 权限或账号状态——{_brief(e)}"
        if code == 404:
            return LLMFatalError, f"DeepSeek 模型不存在（404）：{_brief(e)}——请检查 model 名是否正确"
        if code == 429 or 500 <= code < 600:
            return LLMRetryableError, ""
        # 其余状态码（408/422 等不常见码）落到关键词兜底，仍不命中按 Retryable 处理
    text = f"{type(e).__name__} {e}".lower().replace("_", " ")
    if any(k in text for k in _FATAL_KEYWORDS):
        return LLMFatalError, f"DeepSeek 拒绝了请求：{_brief(e)}——请检查 API key / model 名 / 参数"
    if any(k in text for k in _RETRYABLE_KEYWORDS):
        return LLMRetryableError, ""
    # 普通异常（KeyError/TypeError/OSError 等，根因不明）→ 归 Retryable。
    # 理由：误判 Retryable 的代价是 3 次退避重试（约 12 秒）后照旧抛错，调用方
    # 看到的最终行为与旧版一致；误判 Fatal 则直接终止整个批量生成任务，代价不对称，
    # 保守起见默认可重试。
    return LLMRetryableError, ""


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            # 认证类致命错：重试无意义，直接抛 Fatal（继承 RuntimeError，调用方宽捕获
            # 行为不变；chat 循环内经 _classify_error 的 isinstance 分支原样沿用类与消息）
            raise LLMFatalError(
                "DEEPSEEK_API_KEY 未设置：请确认 mathforge/.env 存在且含 key"
            )
        # 重试策略收归本层（模块 A 单层化），SDK 不做隐式重试：max_retries=0 关掉
        # SDK 默认的 2 次内部重试（否则与本层 chat 的 3 次退避叠加，宕机时单请求
        # 降级实测 68 秒）；timeout=60.0 为单次请求超时——SDK 抛出的超时异常
        # （消息含 "timed out"）经 _classify_error 落 Retryable 分支，由 chat 的
        # 分类退避重试接管。
        _client = OpenAI(api_key=key, base_url=BASE_URL, max_retries=0, timeout=60.0)
    return _client


def _cache_path(key: str) -> Path:
    # 缓存目录可覆写（模块 B：测试/多实例隔离）——每次调用读环境变量
    # MATHFORGE_CACHE_DIR，设置则用该目录；未设置退回模块默认 CACHE_DIR（行为不变）。
    # 刻意不用 lru_cache：缓存目录要能随测试逐个切换，避免测试间状态泄漏。
    env_dir = os.environ.get("MATHFORGE_CACHE_DIR")
    base = Path(env_dir) if env_dir else CACHE_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{key}.json"


def chat(messages: list[dict], model: str = DEFAULT_MODEL, temperature: float = 1.0,
         response_json: bool = False, use_cache: bool = True, namespace: str = "") -> str:
    """调用 DeepSeek chat；带磁盘缓存与错误分类重试。返回 assistant 文本。

    - Fatal（认证 401 / 参数 400 / 模型 404）：立即抛 LLMFatalError，0 次重试
    - Retryable（限流 429 / 5xx / 超时 / 网络 / 空响应）：按 2*(attempt+1) 秒退避重试，
      3 次耗尽后抛 LLMRetryableError（附最后一次错误摘要）
    - 空响应加固（模块 M4，用户实测 p003 连续空回）：HTTP 200 但 content 为空串/纯空白
      与「choices 为空」同类瞬态——抛 Retryable 走既有退避链；空串本身不落缓存（否则
      空回被钉死在缓存键上，后续命中永远拿到空）；退避链中出现空回后的成功结果落
      「独立缓存键」（键串附加空回重试标记再哈希，与 attempt 命名空间机制同一思想：
      重试是不同采样、键也独立）。落点定在 llm.chat 而非 generate 侧：chat 是唯一 LLM
      入口（AGENTS §4），绿标/盲解/解析/变式/重算全体调用方同受益，且 generate 侧只能
      看到 chat_json 的解析失败，无法区分「空回」与「坏 JSON」两种瞬态。
      三条既有语义不变：缓存命中路径（含历史空串条目）原样返回、零重试；demo 只读缓存
      语义不变（空回分支只在网络新采路径可达，demo 未命中在循环前已报错）；重试分类
      （Fatal 0 重试 / Retryable 3 次退避）不变。
    - 缓存命中不触网；MATHFORGE_DEMO=1 只读缓存，未命中直接报错（不静默降级）
    """
    payload = {"m": messages, "model": model, "t": temperature, "j": response_json}
    key = hashlib.sha256((namespace + json.dumps(payload, ensure_ascii=False)).encode()).hexdigest()[:40]
    cp = _cache_path(key)
    if use_cache and cp.exists():
        return json.loads(cp.read_text(encoding="utf-8"))["text"]
    if os.environ.get("MATHFORGE_DEMO") == "1":
        raise RuntimeError(f"demo 模式且缓存未命中（{key}）——演示数据需预热")

    last_err: Exception | None = None
    last_cls: type[RuntimeError] = LLMRetryableError
    empty_seen = False  # 退避链中出现过空响应 → 成功结果落独立缓存键（见 docstring）
    for attempt in range(3):
        try:
            kwargs = {"model": model, "messages": messages, "temperature": temperature}
            if response_json:
                kwargs["response_format"] = {"type": "json_object"}
            resp = _get_client().chat.completions.create(**kwargs)
            if not getattr(resp, "choices", None):
                # 「调用成功但结构异常」（HTTP 200 但 choices 为空）：单独归 Retryable——
                # 请求本身被接受，换一次采样大概率拿到正常结构，重试有意义（不同于参数/认证类致命错）。
                raise LLMRetryableError("DeepSeek 返回 200 但 choices 为空（结构异常）")
            text = resp.choices[0].message.content or ""
            if not text.strip():
                # 空响应（模块 M4）：模型偶发空回，与 choices 为空同类瞬态 → Retryable
                # 走退避重试；空串不落缓存（钉死缓存键会让重试失去意义，见 docstring）。
                empty_seen = True
                raise LLMRetryableError("DeepSeek 返回空响应（HTTP 200 但内容为空）")
            # 空回后重试成功的采样落独立缓存键（键串附加重试标记再哈希——合法文件名，
            # 与原键必不同）；无空回的常规路径键不变，历史行为逐字节兼容。
            out_cp = _cache_path(hashlib.sha256((key + ":empty-retry").encode()).hexdigest()[:40]) \
                if empty_seen else cp
            out_cp.write_text(json.dumps({"text": text, "model": model, "ts": time.time()},
                                         ensure_ascii=False), encoding="utf-8")
            return text
        except Exception as e:  # noqa: BLE001 —— 统一分类：Fatal 立即失败，Retryable 退避重试
            last_cls, hint = _classify_error(e)
            last_err = e
            if last_cls is LLMFatalError:
                raise last_cls(hint or _brief(e)) from e
            time.sleep(2 * (attempt + 1))
    raise last_cls(f"DeepSeek 调用失败（重试 3 次耗尽）：{_brief(last_err)}") from last_err


def chat_json(messages: list[dict], **kw) -> dict:
    """chat 的 JSON 便捷封装：解析失败时修复非法转义与 markdown 围栏后重试。

    chat 抛出的 LLMFatalError 会原样向上传播（继承 RuntimeError，不吞不降级）；
    JSON 解析失败仍抛 ValueError，行为不变。
    """
    import re

    text = chat(messages, response_json=True, **kw)

    def try_parse(s: str):
        return json.loads(s)

    candidates = [text,
                  text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")]
    # 修复 LLM 常见的非法转义（如 JSON 字符串里裸写 \dfrac）
    fixed = re.sub(r'\\(?![u"\\/bfnrt])', r'\\\\', text)
    candidates.append(fixed)
    for c in candidates:
        try:
            return try_parse(c)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"LLM 输出无法解析为 JSON：{text[:120]}")
