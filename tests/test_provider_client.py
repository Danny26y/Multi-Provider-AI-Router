import asyncio

from app.core.circuit_breaker import circuit_breaker
from app.schemas.requests import ChatCompletionRequest
from app.services.provider_client import MultiProviderClient


def request():
    return ChatCompletionRequest(messages=[{"role": "user", "content": "Hello"}])


def test_missing_credentials_are_reported_before_mock_fallback():
    circuit_breaker._states.clear()
    client = MultiProviderClient()
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
    client.provider_configs = {
        "first": {"base_url": "https://example.test", "api_key": "test", "default_model": "first"},
        "second": {"base_url": "https://example.test", "api_key": "test", "default_model": "second"},
        "mock_fallback": {"is_mock": True},
    }
    attempted = []

    async def fake_send(config, _request):
        attempted.append(config["default_model"])
        return False, 503, "http_error"

    monkeypatch.setattr(client, "_send_http_request", fake_send)
    response = asyncio.run(client.execute_chat_completion(request()))

    assert attempted == ["first", "second"]
    assert response.provider_used == "mock_fallback"
    assert response.fallback_used is True
    assert [item.outcome for item in response.routing_diagnostics] == ["failed", "failed", "success"]


def test_successful_provider_response_is_not_marked_as_fallback(monkeypatch):
    circuit_breaker._states.clear()
    client = MultiProviderClient()
    client.provider_configs = {
        "first": {"base_url": "https://example.test", "api_key": "test", "default_model": "first"},
        "mock_fallback": {"is_mock": True},
    }

    async def fake_send(_config, _request):
        return True, {
            "id": "provider-response",
            "created": 1,
            "model": "first",
            "choices": [{"message": {"role": "assistant", "content": "Hi"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }, "success"

    monkeypatch.setattr(client, "_send_http_request", fake_send)
    response = asyncio.run(client.execute_chat_completion(request()))

    assert response.provider_used == "first"
    assert response.fallback_used is False
    assert response.routing_diagnostics[0].outcome == "success"
