import asyncio

import httpx
import pytest
from fastapi import HTTPException

import main
from app.schemas.requests import ChatCompletionRequest
from config.settings import settings


def request(stream=False):
    return ChatCompletionRequest(messages=[{"role": "user", "content": "Hello"}], stream=stream)


def api_request(method, path, **kwargs):
    async def send():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_liveness_is_public():
    response = api_request("GET", "/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "online"}


def test_health_does_not_expose_circuit_state_and_metrics_are_available():
    health = api_request("GET", "/health")
    metrics = api_request("GET", "/metrics")

    assert health.status_code == 200
    assert health.json() == {"status": "online"}
    assert metrics.status_code == 200
    assert "ai_router_http_requests_total" in metrics.text


def test_request_correlation_id_is_returned():
    response = api_request("GET", "/health/live", headers={"X-Request-ID": "test-request-id"})

    assert response.headers["X-Request-ID"] == "test-request-id"


def test_chat_requires_configured_api_key(monkeypatch):
    monkeypatch.setattr(settings, "ROUTER_API_KEY", "router-test-key")

    response = api_request("POST", "/v1/chat/completions", json={"messages": [{"role": "user", "content": "Hello"}]})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API key."


def test_request_validation_and_streaming_rejection(monkeypatch):
    monkeypatch.setattr(settings, "ROUTER_API_KEY", "router-test-key")
    headers = {"X-API-Key": "router-test-key"}

    invalid = api_request("POST", "/v1/chat/completions", headers=headers, json={"messages": []})
    streaming = api_request(
        "POST",
        "/v1/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "Hello"}], "stream": True},
    )

    assert invalid.status_code == 422
    assert streaming.status_code == 400
    assert streaming.json()["detail"] == "Streaming responses are not supported."


def test_provider_failure_returns_generic_service_error(monkeypatch):
    async def fail(_request):
        raise RuntimeError("sensitive upstream response")

    monkeypatch.setattr(main.provider_client, "execute_chat_completion", fail)

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.chat_completions(request()))

    assert error.value.status_code == 503
    assert error.value.detail == "No AI provider is currently available."


def test_in_process_rate_limiter_returns_retry_after():
    limiter = main.ClientRequestLimiter(requests_per_minute=1, max_concurrent_requests=1)

    async def check_limit():
        assert await limiter.check_rate_limit("client") is None
        assert await limiter.check_rate_limit("client") is not None

    asyncio.run(check_limit())


def test_readiness_reports_unready_without_eligible_configuration(monkeypatch):
    monkeypatch.setattr(main.provider_client, "is_ready", lambda: False)

    response = api_request("GET", "/health/ready")

    assert response.status_code == 503
