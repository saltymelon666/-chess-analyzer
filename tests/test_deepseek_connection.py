import httpx
import pytest

from app.config import DEFAULT_DEEPSEEK_MODEL, OFFICIAL_DEEPSEEK_BASE_URL, load_settings
from app.deepseek_connection import check_deepseek_connection, status_message


def test_settings_trim_secret_values_and_migrate_legacy_model(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "  sk-test-placeholder  ")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "  https://api.deepseek.com/  ")
    monkeypatch.setenv("DEEPSEEK_MODEL", " deepseek-chat ")
    settings = load_settings()
    assert settings.deepseek_api_key == "sk-test-placeholder"
    assert settings.deepseek_base_url == OFFICIAL_DEEPSEEK_BASE_URL
    assert settings.deepseek_model == DEFAULT_DEEPSEEK_MODEL


@pytest.mark.asyncio
async def test_connection_check_rejects_missing_or_wrong_prefix_without_network(monkeypatch) -> None:
    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("missing key must not make a request")

    monkeypatch.setattr(httpx, "AsyncClient", ForbiddenClient)
    missing = await check_deepseek_connection(
        api_key="", base_url=OFFICIAL_DEEPSEEK_BASE_URL, model=DEFAULT_DEEPSEEK_MODEL
    )
    invalid = await check_deepseek_connection(
        api_key="not-a-key", base_url=OFFICIAL_DEEPSEEK_BASE_URL, model=DEFAULT_DEEPSEEK_MODEL
    )
    assert missing.message == "未配置DeepSeek API Key"
    assert invalid.message == "API Key无效或未正确加载"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "API Key无效或未正确加载"),
        (402, "DeepSeek账户余额不足"),
        (404, "接口地址或模型名称错误"),
        (429, "请求频率过高"),
        (500, "稍后重试"),
        (503, "稍后重试"),
    ],
)
async def test_connection_check_maps_safe_http_status_messages(monkeypatch, status, expected) -> None:
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            return httpx.Response(status, request=httpx.Request("POST", url), json={"error": "hidden"})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    result = await check_deepseek_connection(
        api_key="sk-test-placeholder",
        base_url=OFFICIAL_DEEPSEEK_BASE_URL,
        model=DEFAULT_DEEPSEEK_MODEL,
    )
    assert result.status_code == status
    assert expected in result.message
    assert "sk-test-placeholder" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_connection_check_returns_usage_without_returning_credentials(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            assert kwargs["headers"]["Authorization"] == "Bearer sk-test-placeholder"
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    result = await check_deepseek_connection(
        api_key="  sk-test-placeholder  ",
        base_url=OFFICIAL_DEEPSEEK_BASE_URL,
        model=DEFAULT_DEEPSEEK_MODEL,
    )
    assert result.status_code == 200
    assert result.message == "DeepSeek连接正常"
    assert result.total_tokens == 5
    assert "sk-test-placeholder" not in result.model_dump_json()


def test_unknown_http_status_is_safe() -> None:
    assert status_message(418) == "DeepSeek连接失败（HTTP 418）"
