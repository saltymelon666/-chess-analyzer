from __future__ import annotations

import time
from typing import Any

import httpx

from .models import DeepSeekConnectionResult


STATUS_MESSAGES = {
    401: "API Key无效或未正确加载",
    402: "DeepSeek账户余额不足",
    404: "接口地址或模型名称错误",
    429: "请求频率过高",
    500: "DeepSeek服务暂时异常，请稍后重试",
    503: "DeepSeek服务暂时不可用，请稍后重试",
}


async def check_deepseek_connection(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: float = 30.0,
) -> DeepSeekConnectionResult:
    """Send a minimal request without exposing the key or response headers."""
    key = api_key.strip()
    if not key:
        return _result(0, "未配置DeepSeek API Key", 0, model)
    if not key.startswith("sk-"):
        return _result(0, "API Key无效或未正确加载", 0, model)

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=max(5.0, timeout_seconds)) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "只回复小写英文ok。"},
                        {"role": "user", "content": "连接测试"},
                    ],
                    "max_tokens": 8,
                    "temperature": 0,
                },
            )
        elapsed = round((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            return _result(
                response.status_code,
                status_message(response.status_code),
                elapsed,
                model,
            )
        data = response.json()
        usage = data.get("usage") or {}
        return DeepSeekConnectionResult(
            status_code=200,
            message="DeepSeek连接正常",
            elapsed_ms=elapsed,
            model=model,
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(usage.get("completion_tokens")),
            total_tokens=_optional_int(usage.get("total_tokens")),
        )
    except httpx.TimeoutException:
        return _result(504, "DeepSeek连接超时，请稍后重试", round((time.perf_counter() - started) * 1000), model)
    except httpx.RequestError:
        return _result(503, STATUS_MESSAGES[503], round((time.perf_counter() - started) * 1000), model)
    except (ValueError, TypeError):
        return _result(502, "DeepSeek返回格式异常", round((time.perf_counter() - started) * 1000), model)


def status_message(status_code: int) -> str:
    if status_code in STATUS_MESSAGES:
        return STATUS_MESSAGES[status_code]
    if status_code >= 500:
        return "DeepSeek服务暂时异常，请稍后重试"
    return f"DeepSeek连接失败（HTTP {status_code}）"


def _result(status_code: int, message: str, elapsed_ms: int, model: str) -> DeepSeekConnectionResult:
    return DeepSeekConnectionResult(
        status_code=status_code,
        message=message,
        elapsed_ms=elapsed_ms,
        model=model,
    )


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
