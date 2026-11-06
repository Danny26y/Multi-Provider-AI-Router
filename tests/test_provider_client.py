import asyncio

import httpx
import pytest
from app.core.circuit_breaker import ProviderStatus, circuit_breaker
from app.schemas.requests import ChatCompletionRequest
from app.services.provider_client import MultiProviderClient
from config.settings import settings


def request(**kwargs):
    return ChatCompletionRequest(messages=[{"role": "user", "content": "Hello"}], **kwargs)


def test_mock_fallback_is_disabled_by_default():
    circuit_breaker._states.clear()
    client = MultiProviderClient()
    client.provider_configs = {"mock_fallback": {"is_mock": True}}

    try:
        asyncio.run(client.execute_chat_completion(request()))
    except RuntimeError as error:
        assert "No eligible AI provider" in str(error)
    else:
        raise AssertionError("Mock fallback must be disabled by default.")


def test_missing_credentials_are_reported_before_enabled_mock_fallback():
    circuit_breaker._states.clear()
    client = MultiProviderClient()
    client.mock_fallback_enabled = True
    client.provider_configs = {
        "first": {"base_url": "https://example.test", "api_key": None, "default_model": "first"},
        "second": {"base_url": "https://example.test", "api_key": None, "default_model": "second"},
        "mock_fallback": {"is_mock": True},
    }

    response = asyncio.run(client.execute_chat_completion(request()))

    assert response.provider_used == "mock_fallback"
    assert response.fallback_used is True
    assert [(item.provider, item.category) for item in response.routing_diagnostics] == [
        ("first", "missing_credentials"),
        ("second", "missing_credentials"),
        ("mock_fallback", "mock_fallback"),
    ]


def test_provider_failures_follow_configured_order_then_fallback(monkeypatch):
    circuit_breaker._states.clear()
    client = MultiProviderClient()
    client.mock_fallback_enabled = True
    client.provider_configs = {
        "first": {"base_url": "https://example.test", "api_key": "test", "default_model": "first"},
        "second": {"base_url": "https://example.test", "api_key": "test", "default_model": "second"},
        "mock_fallback": {"is_mock": True},
    }
    attempted = []

    async def fake_send(config, _request, _remaining_seconds):
        attempted.append(config["default_model"])
        return False, 503, "server_error", True, None

    monkeypatch.setattr(client, "_send_http_request", fake_send)
    response = asyncio.run(client.execute_chat_completion(request()))

    assert attempted == ["first", "second"]
    assert response.provider_used == "mock_fallback"
    assert [item.outcome for item in response.routing_diagnostics] == ["failed", "failed", "success"]


def test_non_retryable_provider_failure_does_not_open_circuit(monkeypatch):
    circuit_breaker._states.clear()
    client = MultiProviderClient()
    client.mock_fallback_enabled = True
    client.provider_configs = {
        "first": {"base_url": "https://example.test", "api_key": "test", "default_model": "first"},
        "mock_fallback": {"is_mock": True},
    }

    async def fake_send(_config, _request, _remaining_seconds):
        return False, 401, "auth_error", False, None

    monkeypatch.setattr(client, "_send_http_request", fake_send)
    response = asyncio.run(client.execute_chat_completion(request()))

    assert response.provider_used == "mock_fallback"
    state = circuit_breaker.snapshot()["first"]
    assert state["failures"] == 0
    assert state["status"] == ProviderStatus.HEALTHY


def test_malformed_upstream_response_fails_over(monkeypatch):
    circuit_breaker._states.clear()
    client = MultiProviderClient()
    client.mock_fallback_enabled = True
    client.provider_configs = {
        "first": {"base_url": "https://example.test", "api_key": "test", "default_model": "first"},
        "mock_fallback": {"is_mock": True},
    }

    async def fake_send(_config, _request, _remaining_seconds):
        return True, {"id": "bad", "choices": []}, "success", True, None

    monkeypatch.setattr(client, "_send_http_request", fake_send)
    response = asyncio.run(client.execute_chat_completion(request()))

    assert response.provider_used == "mock_fallback"
    assert response.routing_diagnostics[0].category == "invalid_response"


def test_explicit_model_routes_only_to_compatible_provider(monkeypatch):
    circuit_breaker._states.clear()
    client = MultiProviderClient()
    client.provider_configs = {
        "first": {"base_url": "https://example.test", "api_key": "test", "default_model": "llama-3.1-8b-instant"},
        "second": {"base_url": "https://example.test", "api_key": "test", "default_model": "zai-glm-4.7"},
    }
    attempted = []

    async def fake_send(config, _request, _remaining_seconds):
        attempted.append(config["default_model"])
        return True, {
            "id": "provider-response",
            "created": 1,
            "model": config["default_model"],
            "choices": [{"message": {"role": "assistant", "content": "Hi"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }, "success", True, None

    monkeypatch.setattr(client, "_send_http_request", fake_send)
    response = asyncio.run(client.execute_chat_completion(request(model="zai-glm-4.7")))

    assert attempted == ["zai-glm-4.7"]
    assert response.provider_used == "second"


def test_http_client_is_reused_until_lifespan_shutdown():
    client = MultiProviderClient()

    async def manage_client():
        await client.start()
        first_client = client.http_client
        await client.start()
        assert client.http_client is first_client
        await client.close()
        assert client.http_client is None

    asyncio.run(manage_client())


def test_actual_usage_and_configured_price_are_reported(monkeypatch):
    client = MultiProviderClient()
    monkeypatch.setattr(settings, "MODEL_PRICES_USD_PER_MILLION_TOKENS", {"test-model": 2.0})
    response = client._build_response(
        {
            "id": "provider-response",
            "created": 1,
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "Hi"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        },
        {"default_model": "test-model"},
        "provider",
        request(),
        0.1,
        [],
    )

    assert response.usage.token_usage_is_estimated is False
    assert response.usage.estimated_cost_usd == 0.00001


def test_retry_after_is_capped_to_configured_limit(monkeypatch):
    client = MultiProviderClient()
    monkeypatch.setattr(settings, "MAX_UPSTREAM_RETRY_AFTER_SECONDS", 10.0)

    assert client._parse_retry_after("20") == 10.0
    assert client._parse_retry_after("not-a-date") is None


@pytest.mark.parametrize(
    ("response", "expected_category", "expected_retryable"),
    [
        (httpx.Response(429, headers={"Retry-After": "30"}), "rate_limited", True),
        (httpx.Response(500), "server_error", True),
        (httpx.Response(200, content=b"not-json"), "invalid_response", True),
    ],
)
def test_http_transport_classifies_retryable_upstream_responses(response, expected_category, expected_retryable):
    client = MultiProviderClient()

    async def send_request():
        client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response))
        try:
            return await client._send_http_request(
                {"base_url": "https://example.test", "api_key": "test", "default_model": "test-model"},
                request(),
                5.0,
            )
        finally:
            await client.close()

    result = asyncio.run(send_request())

    assert result[2] == expected_category
    assert result[3] is expected_retryable


def test_http_transport_classifies_timeouts():
    client = MultiProviderClient()

    async def timeout_handler(_request):
        raise httpx.ReadTimeout("timeout")

    async def send_request():
        client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
        try:
            return await client._send_http_request(
                {"base_url": "https://example.test", "api_key": "test", "default_model": "test-model"},
                request(),
                5.0,
            )
        finally:
            await client.close()

    result = asyncio.run(send_request())

    assert result[2] == "timeout"
    assert result[3] is True


def test_complete_outage_raises_without_enabled_fallback(monkeypatch):
    circuit_breaker._states.clear()
    client = MultiProviderClient()
    client.provider_configs = {
        "first": {"base_url": "https://example.test", "api_key": "test", "default_model": "first"},
        "second": {"base_url": "https://example.test", "api_key": "test", "default_model": "second"},
    }

    async def failed_send(_config, _request, _remaining_seconds):
        return False, 503, "server_error", True, None

    monkeypatch.setattr(client, "_send_http_request", failed_send)

    with pytest.raises(RuntimeError, match="No eligible AI provider"):
        asyncio.run(client.execute_chat_completion(request()))
