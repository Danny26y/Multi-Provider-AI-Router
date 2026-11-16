"""Deterministic transport fixtures; these tests never contact a live provider."""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.core.circuit_breaker import circuit_breaker
from app.schemas.requests import ChatCompletionRequest
from app.services.provider_client import MultiProviderClient
from config.settings import Settings, settings
from tests.test_nvidia_provider import completion, request, run


@pytest.fixture
def client(monkeypatch):
    circuit_breaker._states.clear()
    monkeypatch.setattr(settings, "NVIDIA_ENABLED", True)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test")
    monkeypatch.setattr(settings, "PROVIDER_ORDER", ["groq_fast", "nvidia", "groq_reasoning"])
    yield MultiProviderClient()
    circuit_breaker._states.clear()


@pytest.mark.parametrize("body,category", [
    ({"error": {"code": "model_not_found", "message": "private"}}, "unsupported_model"),
    ({"error": {"code": "other", "message": "private"}}, "upstream_not_found"),
    ({"error": []}, "upstream_not_found"),
    ([], "upstream_not_found"),
    ("not JSON", "upstream_not_found"),
    ("x" * 9000, "upstream_not_found"),
])
def test_404_codes_are_bounded_sanitized_and_fail_over(client, body, category, caplog):
    client.provider_configs = {"groq_fast": client.provider_configs["groq_fast"], "nvidia": client.provider_configs["nvidia"]}
    client.provider_configs["groq_fast"]["api_key"] = "test"

    def handler(req):
        if req.url.host == "api.groq.com":
            return httpx.Response(404, json=body) if not isinstance(body, str) else httpx.Response(404, text=body)
        return httpx.Response(200, json=completion())

    response = run(client, handler)
    assert response.provider_used == "nvidia" and response.fallback_used
    assert response.routing_diagnostics[0].category == category
    assert "private" not in response.model_dump_json() + caplog.text


@pytest.mark.parametrize("thinking,tokens,budget", [(False, 128, None), (True, 128, 64), (True, 1024, 512), (True, 4096, 1024), (True, 1, None)])
def test_nvidia_explicit_wire_controls(client, thinking, tokens, budget):
    config = client.provider_configs["nvidia"]
    config["enable_thinking"] = thinking
    client.provider_configs = {"nvidia": config}

    def handler(req):
        payload = json.loads(req.content)
        assert payload["stream"] is False
        assert payload["max_tokens"] == tokens
        assert payload["chat_template_kwargs"] == {"enable_thinking": thinking and tokens > 1}
        assert payload.get("reasoning_budget") == budget
        assert req.extensions["timeout"]["read"] == 30
        return httpx.Response(200, json=completion(reasoning="private", reasoning_content="private"))

    assert "private" not in run(client, handler, request(max_tokens=tokens)).model_dump_json()


@pytest.mark.parametrize("model,provider", [("openai/gpt-oss-20b", "groq_fast"), ("openai/gpt-oss-120b", "groq_reasoning")])
def test_groq_defaults_validate_and_parse(client, model, provider):
    defaults = Settings(_env_file=None)
    assert getattr(defaults, "GROQ_FAST_MODEL" if provider == "groq_fast" else "GROQ_REASONING_MODEL") == model
    config = client.provider_configs[provider]
    config["api_key"] = "test"
    client.provider_configs = {provider: config}

    def handler(req):
        payload = json.loads(req.content)
        assert payload["model"] == model
        assert payload["reasoning_effort"] == "low"
        assert payload["stream"] is False
        assert "reasoning_format" not in payload
        return httpx.Response(200, json=completion(model=model, reasoning="private"))

    assert run(client, handler, request(model=model)).provider_used == provider


@pytest.mark.parametrize("change,category", [
    ("reasoning_length", "unusable_output"), ("empty", "unusable_output"),
    ("model", "model_mismatch"), ("malformed", "invalid_response"),
])
def test_nvidia_failure_falls_back_to_real_groq(client, change, category, caplog):
    client.provider_configs = {"nvidia": client.provider_configs["nvidia"], "groq_reasoning": client.provider_configs["groq_reasoning"]}
    client.provider_configs["groq_reasoning"]["api_key"] = "test"
    body = completion()
    if change == "reasoning_length":
        body["choices"][0].update(finish_reason="length")
        body["choices"][0]["message"].update(content=None, reasoning_content="private")
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 128, "total_tokens": 138,
                         "completion_tokens_details": {"reasoning_tokens": 128}}
    elif change == "empty":
        body["choices"][0]["message"]["content"] = ""
    elif change == "model":
        body["model"] = "unexpected"
    else:
        body["choices"] = [None]

    def handler(req):
        return httpx.Response(200, json=body if req.url.host == "integrate.api.nvidia.com" else completion(model=settings.GROQ_REASONING_MODEL))

    response = run(client, handler)
    assert response.provider_used == "groq_reasoning" and response.fallback_used
    assert response.routing_diagnostics[0].category == category
    assert "private" not in caplog.text + response.model_dump_json()


@pytest.mark.parametrize("field,value", [
    ("TOTAL_REQUEST_TIMEOUT_SECONDS", 181), ("TOTAL_REQUEST_TIMEOUT_SECONDS", float("inf")),
    ("NVIDIA_READ_TIMEOUT_SECONDS", 121), ("NVIDIA_READ_TIMEOUT_SECONDS", 0),
    ("NVIDIA_ATTEMPT_TIMEOUT_SECONDS", 121), ("NVIDIA_REASONING_BUDGET", -1),
])
def test_new_settings_are_bounded(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_attempt_timeout_leaves_room_for_fallback(client):
    client.provider_configs = {"nvidia": client.provider_configs["nvidia"], "groq_reasoning": client.provider_configs["groq_reasoning"]}
    client.provider_configs["nvidia"]["attempt_timeout"] = 0.01
    client.provider_configs["groq_reasoning"]["api_key"] = "test"
    cancelled = []

    async def handler(req):
        if req.url.host == "integrate.api.nvidia.com":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        return httpx.Response(200, json=completion(model=settings.GROQ_REASONING_MODEL))

    response = run(client, handler)
    assert cancelled == [True]
    assert response.provider_used == "groq_reasoning"
    assert response.routing_diagnostics[0].category == "timeout"


def test_new_nvidia_config_is_wired(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_ENABLED", True)
    monkeypatch.setattr(settings, "NVIDIA_READ_TIMEOUT_SECONDS", 45)
    monkeypatch.setattr(settings, "NVIDIA_ATTEMPT_TIMEOUT_SECONDS", 50)
    config = MultiProviderClient().provider_configs["nvidia"]
    assert (config["read_timeout"], config["attempt_timeout"]) == (45, 50)
    ChatCompletionRequest(messages=[{"role": "user", "content": "Hello"}])


@pytest.mark.parametrize("text", ["<think>private</think>Hello", "private</think>Hello", "<think>private"])
def test_unparsed_reasoning_is_never_public(client, text):
    from app.services.routing_errors import RoutingError

    client.provider_configs = {"nvidia": client.provider_configs["nvidia"]}
    with pytest.raises(RoutingError) as error:
        run(client, lambda _: httpx.Response(200, json=completion(content=text)))
    assert error.value.diagnostics[0].category == "unusable_output"
    assert "private" not in str(error.value)


@pytest.mark.parametrize("content,exit_code", [("Hello", 0), (None, 1), ("", 1)])
def test_live_probe_requires_valid_completion_without_live_network(client, monkeypatch, capsys, content, exit_code):
    from scripts import nvidia_smoke

    client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=completion(content=content, reasoning_content="private")),
    ))
    monkeypatch.setattr(nvidia_smoke, "MultiProviderClient", lambda: client)
    assert asyncio.run(nvidia_smoke.probe()) == exit_code
    assert "private" not in capsys.readouterr().out
