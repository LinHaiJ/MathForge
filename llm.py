"""LLM 公共客户端：DeepSeek chat API + 全量磁盘缓存。

- key 从 mathforge/.env 读取（gitignored），只发往 DeepSeek 官方端点
- 所有调用写 cache/llm/<hash>.json → 演示模式零 API 依赖（PRD 演示规格）
- MATHFORGE_DEMO=1 时只读缓存，缓存未命中直接报错（不静默降级）
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


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY 未设置：请确认 mathforge/.env 存在且含 key"
            )
        _client = OpenAI(api_key=key, base_url=BASE_URL)
    return _client


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def chat(messages: list[dict], model: str = DEFAULT_MODEL, temperature: float = 1.0,
         response_json: bool = False, use_cache: bool = True, namespace: str = "") -> str:
    """调用 DeepSeek chat；带磁盘缓存与两次重试。返回 assistant 文本。"""
    payload = {"m": messages, "model": model, "t": temperature, "j": response_json}
    key = hashlib.sha256((namespace + json.dumps(payload, ensure_ascii=False)).encode()).hexdigest()[:40]
    cp = _cache_path(key)
    if use_cache and cp.exists():
        return json.loads(cp.read_text(encoding="utf-8"))["text"]
    if os.environ.get("MATHFORGE_DEMO") == "1":
        raise RuntimeError(f"demo 模式且缓存未命中（{key}）——演示数据需预热")

    last_err = None
    for attempt in range(3):
        try:
            kwargs = {"model": model, "messages": messages, "temperature": temperature}
            if response_json:
                kwargs["response_format"] = {"type": "json_object"}
            resp = _get_client().chat.completions.create(**kwargs)
            text = resp.choices[0].message.content or ""
            cp.write_text(json.dumps({"text": text, "model": model, "ts": time.time()},
                                     ensure_ascii=False), encoding="utf-8")
            return text
        except Exception as e:  # noqa: BLE001 —— 网络/限流统一重试
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"DeepSeek 调用失败（3 次）：{last_err}")


def chat_json(messages: list[dict], **kw) -> dict:
    """chat 的 JSON 便捷封装：解析失败时修复非法转义与 markdown 围栏后重试。"""
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
