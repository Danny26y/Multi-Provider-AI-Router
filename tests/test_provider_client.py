import asyncio

from app.core.circuit_breaker import circuit_breaker
from app.schemas.requests import ChatCompletionRequest
from app.services.provider_client import MultiProviderClient


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

    async def fake_send(config, _request):
        attempted.append(config["default_model"])
        return False, 503, "server_error", True

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

    async def fake_send(_config, _request):
        return False, 401, "auth_error", False

    monkeypatch.setattr(client, "_send_http_request", fake_send)
    response = asyncio.run(client.execute_chat_completion(request()))

    assert response.provider_used == "mock_fallback"
    assert "first" not in circuit_breaker._states


def test_malformed_upstream_response_fails_over(monkeypatch):
    circuit_breaker._states.clear()
    client = MultiProviderClient()
    client.mock_fallback_enabled = True
    client.provider_configs = {
        "first": {"base_url": "https://example.test", "api_key": "test", "default_model": "first"},
        "mock_fallback": {"is_mock": True},
    }

    async def fake_send(_config, _request):
        return True, {"id": "bad", "choices": []}, "success", True

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

    async def fake_send(config, _request):
        attempted.append(config["default_model"])
        return True, {
            "id": "provider-response",
            "created": 1,
            "model": config["default_model"],
            "choices": [{"message": {"role": "assistant", "content": "Hi"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }, "success", True

    monkeypatch.setattr(client, "_send_http_request", fake_send)
    response = asyncio.run(client.execute_chat_completion(request(model="zai-glm-4.7")))

    assert attempted == ["zai-glm-4.7"]
    assert response.provider_used == "second"
