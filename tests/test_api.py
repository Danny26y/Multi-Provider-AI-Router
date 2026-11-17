import asyncio
import json
import logging

import httpx
import pytest
from fastapi import HTTPException

import main
from app.core.circuit_breaker import CircuitBreaker
from app.schemas.requests import ChatCompletionRequest
from app.services.provider_client import MultiProviderClient
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


@pytest.mark.parametrize("nvidia_result,selected_provider,nvidia_category", [
    pytest.param("success", "nvidia", None, id="nvidia_success"),
    pytest.param("server_error", "groq_reasoning", "server_error", id="groq_reasoning_after_nvidia_503"),
    pytest.param("reasoning_only", "groq_reasoning", "unusable_output", id="groq_reasoning_after_reasoning_only"),
    pytest.param("model_mismatch", "groq_reasoning", "model_mismatch", id="groq_reasoning_after_model_mismatch"),
    pytest.param("malformed", "groq_reasoning", "invalid_response", id="groq_reasoning_after_malformed_response"),
])
def test_api_checkpoint_g_fallback_acceptance(monkeypatch, caplog, nvidia_result, selected_provider, nvidia_category):
    """Actual ASGI endpoint and provider parsing; only upstream transport is mocked."""
    router_key = "acceptance-router-credential"
    groq_key = "acceptance-groq-credential"
    nvidia_key = "acceptance-nvidia-credential"
    private_reasoning = "acceptance-private-reasoning"
    private_prompt = "acceptance-private-prompt"
    monkeypatch.setattr(settings, "ROUTER_API_KEY", router_key)
    monkeypatch.setattr(settings, "GROQ_API_KEY", groq_key)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", nvidia_key)
    monkeypatch.setattr(settings, "NVIDIA_ENABLED", True)
    monkeypatch.setattr(settings, "CEREBRAS_ENABLED", False)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "ENABLE_MOCK_FALLBACK", False)
    monkeypatch.setattr(settings, "PROVIDER_ORDER", ["groq_fast", "nvidia", "groq_reasoning", "mock_fallback"])
    monkeypatch.setattr(settings, "PROVIDER_MODEL_ALIASES", {})
    breaker = CircuitBreaker()
    monkeypatch.setattr("app.services.provider_client.circuit_breaker", breaker)
    provider = MultiProviderClient()
    monkeypatch.setattr(main, "provider_client", provider)
    monkeypatch.setattr(main, "rate_limiter", main.ClientRequestLimiter(60, 1))
    # The router logger intentionally does not propagate to pytest's root handler.
    # Capture its actual INFO/WARNING records and structured fields explicitly.
    router_logger = logging.getLogger("router")
    monkeypatch.setattr(router_logger, "handlers", [caplog.handler])
    monkeypatch.setattr(router_logger, "propagate", False)
    caplog.set_level(logging.INFO)
    attempted = []
    models = {
        "groq_fast": settings.GROQ_FAST_MODEL,
        "nvidia": settings.NVIDIA_DEFAULT_MODEL,
        "groq_reasoning": settings.GROQ_REASONING_MODEL,
    }

    def handler(req):
        payload = json.loads(req.content)
        name = next(name for name, model in models.items() if model == payload["model"])
        attempted.append(name)
        expected_base = settings.NVIDIA_BASE_URL if name == "nvidia" else settings.GROQ_BASE_URL
        assert str(req.url) == expected_base + "/chat/completions"
        assert req.method == "POST" and payload["stream"] is False
        assert req.headers["Authorization"] == "Bearer " + (nvidia_key if name == "nvidia" else groq_key)
        assert "X-API-Key" not in req.headers
        if name == "groq_fast" or (name == "nvidia" and nvidia_result == "server_error"):
            return httpx.Response(503, text=f"{groq_key} {nvidia_key} {private_reasoning}")
        body = {
            "model": models[name],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": "Accepted visible completion.",
                "reasoning": private_reasoning, "reasoning_content": private_reasoning,
            }}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        if name == "nvidia":
            if nvidia_result == "reasoning_only":
                body["choices"][0]["message"]["content"] = None
                body["choices"][0]["finish_reason"] = "length"
            elif nvidia_result == "model_mismatch":
                body["model"] = "unexpected-model"
            elif nvidia_result == "malformed":
                body["choices"][0]["message"]["content"] = {"text": private_reasoning}
        return httpx.Response(200, json=body)

    async def execute():
        provider.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        # ASGITransport does not run lifespan automatically; exercise it explicitly.
        async with main.app.router.lifespan_context(main.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as api:
                return await api.post("/v1/chat/completions", headers={"X-API-Key": router_key}, json={
                    "messages": [{"role": "user", "content": private_prompt}], "max_tokens": 128,
                })

    response = asyncio.run(execute())
    assert response.status_code == 200
    body = response.json()
    expected_attempts = ["groq_fast", "nvidia"] + (["groq_reasoning"] if selected_provider == "groq_reasoning" else [])
    assert attempted == expected_attempts
    assert body["provider_used"] == selected_provider
    assert body["model"] == models[selected_provider]
    assert body["fallback_used"] is True
    assert body["choices"] == [{"index": 0, "finish_reason": "stop", "message": {
        "role": "assistant", "content": "Accepted visible completion.",
    }}]
    assert body["usage"]["token_usage_is_estimated"] is False
    diagnostics = body["routing_diagnostics"]
    assert [item["provider"] for item in diagnostics] == expected_attempts
    assert [item["category"] for item in diagnostics] == ["server_error"] + (
        [nvidia_category] if selected_provider == "groq_reasoning" else []
    ) + [None]
    assert [item["outcome"] for item in diagnostics] == ["failed"] * (len(expected_attempts) - 1) + ["success"]
    assert all(item["attempted"] for item in diagnostics)
    assert [item["selected"] for item in diagnostics] == [False] * (len(expected_attempts) - 1) + [True]
    assert [item["fallback_used"] for item in diagnostics] == [False] + [True] * (len(expected_attempts) - 1)
    assert breaker.snapshot()["groq_fast"]["failures"] == 1  # Retryable 503 affects provider health.
    assert provider.http_client is None  # Lifespan closed the upstream client.
    events = [getattr(record, "structured_fields", {}) for record in caplog.records]
    assert sum(event.get("event") == "provider_request_failed" for event in events) == len(expected_attempts) - 1
    assert sum(event.get("event") == "provider_request_succeeded" for event in events) == 1
    assert any(event.get("retryable") is True and event.get("category") == "server_error" for event in events)
    captured = response.text + str(dict(response.headers)) + caplog.text + json.dumps(events)
    for sensitive in (router_key, groq_key, nvidia_key, private_reasoning, private_prompt):
        assert sensitive not in captured
