import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.core.circuit_breaker import circuit_breaker
from app.schemas.requests import ChatCompletionRequest
from app.services.provider_client import MultiProviderClient
from config.settings import settings


@pytest.fixture
def client(monkeypatch):
    circuit_breaker._states.clear()
    monkeypatch.setattr(settings, "NVIDIA_ENABLED", True)
    monkeypatch.setattr(settings, "CEREBRAS_ENABLED", False)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "secret-nvidia-test")
    monkeypatch.setattr(settings, "PROVIDER_ORDER", ["groq_fast", "nvidia", "groq_reasoning", "mock_fallback"])
    result = MultiProviderClient()
    yield result
    circuit_breaker._states.clear()


def request(**kwargs):
    return ChatCompletionRequest(messages=[{"role": "user", "content": "Hello"}], **kwargs)


def completion(model=None, content="Hello", **message_fields):
    return {
        "model": model or settings.NVIDIA_DEFAULT_MODEL,
        "choices": [{"message": {"role": "assistant", "content": content, **message_fields}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 100, "total_tokens": 110},
    }


def run(client, handler, req=None):
    async def execute():
        client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.execute_chat_completion(req or request())
        finally:
            await client.close()

    return asyncio.run(execute())


@pytest.mark.parametrize("tokens,expected", [(None, 4096), (512, 512), (12000, 8000)])
def test_nvidia_completion_and_reasoning_budget(client, tokens, expected):
    config = client.provider_configs["nvidia"]
    config["max_output_tokens"] = 8000
    client.provider_configs = {"nvidia": config}

    def handler(req):
        assert str(req.url) == "https://integrate.api.nvidia.com/v1/chat/completions"
        assert req.headers["Authorization"] == "Bearer secret-nvidia-test"
        payload = json.loads(req.content)
        assert payload["max_tokens"] == expected
        assert payload["model"] == settings.NVIDIA_DEFAULT_MODEL
        return httpx.Response(200, json=completion(reasoning_content="internal reasoning"))

    response = run(client, handler, request(**({"max_tokens": tokens} if tokens else {})))
    assert response.provider_used == "nvidia"
    assert response.choices[0].message.content == "Hello"
    assert response.usage.completion_tokens == 100
    assert response.usage.token_usage_is_estimated is False
    assert "internal reasoning" not in response.model_dump_json()


@pytest.mark.parametrize("status,category,retryable", [(429, "rate_limited", True), (401, "auth_error", False), (403, "auth_error", False), (400, "invalid_request", False), (504, "server_error", True)])
def test_nvidia_errors_are_sanitized(client, status, category, retryable, caplog):
    client.provider_configs = {"nvidia": client.provider_configs["nvidia"], "mock_fallback": {"is_mock": True}}
    client.mock_fallback_enabled = True
    response = run(client, lambda _: httpx.Response(status, headers={"Retry-After": "30"}, text="secret-nvidia-test"))
    assert response.routing_diagnostics[0].category == category
    state = circuit_breaker.snapshot()["nvidia"]
    assert bool(state["failures"]) is retryable
    if status == 429:
        assert not circuit_breaker.is_available("nvidia")
    assert "secret-nvidia-test" not in response.model_dump_json() + caplog.text


def test_nvidia_timeout(client):
    client.provider_configs = {"nvidia": client.provider_configs["nvidia"], "mock_fallback": {"is_mock": True}}
    client.mock_fallback_enabled = True

    def timeout(_):
        raise httpx.ReadTimeout("secret-nvidia-test")

    assert run(client, timeout).routing_diagnostics[0].category == "timeout"


def test_missing_nvidia_credentials_never_send(client):
    client.provider_configs = {"nvidia": {**client.provider_configs["nvidia"], "api_key": None}, "mock_fallback": {"is_mock": True}}
    client.mock_fallback_enabled = True

    def forbidden(_):
        pytest.fail("Missing credentials must not reach transport")

    assert run(client, forbidden).routing_diagnostics[0].category == "missing_credentials"


def test_provider_order_and_reasoning_fallback(client):
    assert list(client.provider_configs) == ["groq_fast", "nvidia", "groq_reasoning", "mock_fallback"]
    for config in client.provider_configs.values():
        if not config.get("is_mock"):
            config["api_key"] = "test"
    attempted = []

    def handler(req):
        model = json.loads(req.content)["model"]
        attempted.append(model)
        if model == settings.GROQ_REASONING_MODEL:
            return httpx.Response(200, json=completion(model=model))
        return httpx.Response(503)

    response = run(client, handler)
    assert attempted == [settings.GROQ_FAST_MODEL, settings.NVIDIA_DEFAULT_MODEL, settings.GROQ_REASONING_MODEL]
    assert response.provider_used == "groq_reasoning"


def test_groq_success_prevents_nvidia_call(client):
    client.provider_configs["groq_fast"]["api_key"] = "test"
    attempted = []

    def handler(req):
        attempted.append(str(req.url))
        return httpx.Response(200, json=completion(model=settings.GROQ_FAST_MODEL))

    assert run(client, handler).provider_used == "groq_fast"
    assert len(attempted) == 1


def test_nvidia_is_first_fallback_after_groq_failure(client):
    client.provider_configs["groq_fast"]["api_key"] = "test"
    attempted = []

    def handler(req):
        model = json.loads(req.content)["model"]
        attempted.append(model)
        return httpx.Response(503) if len(attempted) == 1 else httpx.Response(200, json=completion())

    response = run(client, handler)
    assert response.provider_used == "nvidia"
    assert attempted == [settings.GROQ_FAST_MODEL, settings.NVIDIA_DEFAULT_MODEL]
    assert response.routing_diagnostics[0].category == "server_error"


@pytest.mark.parametrize("content", [None, ""])
def test_reasoning_without_visible_answer_fails_over(client, content):
    client.provider_configs = {"nvidia": client.provider_configs["nvidia"], "mock_fallback": {"is_mock": True}}
    client.mock_fallback_enabled = True
    response = run(client, lambda _: httpx.Response(200, json=completion(content=content, reasoning_content="private")))
    assert response.provider_used == "mock_fallback"
    assert response.routing_diagnostics[0].category == "unusable_output"


def test_total_deadline_cancels_inflight_nvidia_request(client, monkeypatch):
    from types import SimpleNamespace

    import app.services.provider_client as module

    clock = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(settings, "TOTAL_REQUEST_TIMEOUT_SECONDS", 0.01)
    client.provider_configs = {"nvidia": client.provider_configs["nvidia"], "mock_fallback": {"is_mock": True}}
    client.mock_fallback_enabled = True
    cancelled = []

    async def handler(req):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
            clock[0] = 1.0

    with pytest.raises(RuntimeError):
        run(client, handler)
    assert cancelled == [True]


def test_explicit_allowed_model_is_sent_unchanged(client, monkeypatch):
    alternate = "nvidia/alternate"
    monkeypatch.setattr(settings, "NVIDIA_ALLOWED_MODELS", [alternate])
    client.provider_configs["nvidia"]["allowed_models"] = [alternate]

    def handler(req):
        assert json.loads(req.content)["model"] == alternate
        return httpx.Response(200, json=completion(model=alternate))

    assert run(client, handler, request(model=alternate)).model == alternate


def test_disallowed_model_cannot_substitute_or_mock(client):
    client.mock_fallback_enabled = True
    client.provider_configs["nvidia"]["allowed_models"] = []

    def forbidden(_):
        pytest.fail("Disallowed model must not reach transport")

    with pytest.raises(RuntimeError):
        run(client, forbidden, request(model=settings.NVIDIA_DEFAULT_MODEL))
    with pytest.raises(ValidationError):
        request(model="unknown/model")


def test_provider_enable_flags(client, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_ENABLED", False)
    monkeypatch.setattr(settings, "CEREBRAS_ENABLED", True)
    monkeypatch.setattr(settings, "PROVIDER_ORDER", ["groq_fast", "nvidia", "cerebras_primary", "groq_reasoning"])
    assert list(MultiProviderClient().provider_configs) == ["groq_fast", "cerebras_primary", "groq_reasoning"]


def test_total_deadline_shared_across_providers(client, monkeypatch):
    import app.services.provider_client as module

    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(settings, "TOTAL_REQUEST_TIMEOUT_SECONDS", 10.0)
    for config in client.provider_configs.values():
        if not config.get("is_mock"):
            config["api_key"] = "test"
    budgets = []

    async def send(config, req, remaining):
        budgets.append(remaining)
        clock[0] += 6
        return False, 504, "timeout", True, None

    monkeypatch.setattr(client, "_send_http_request", send)
    client.mock_fallback_enabled = True
    with pytest.raises(RuntimeError):
        asyncio.run(client.execute_chat_completion(request()))
    assert budgets == [10.0, 4.0]


def test_smoke_import_does_not_make_requests(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Import must not make a live request")

    monkeypatch.setattr(httpx.Client, "post", forbidden)
    path = Path(__file__).parents[1] / "scripts" / "nvidia_smoke.py"
    spec = importlib.util.spec_from_file_location("nvidia_smoke", path)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
