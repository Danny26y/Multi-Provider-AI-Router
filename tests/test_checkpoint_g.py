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
def test_groq_defaults_validate_and_parse(client, model, provider, caplog):
    defaults = Settings(_env_file=None)
    assert getattr(defaults, "GROQ_FAST_MODEL" if provider == "groq_fast" else "GROQ_REASONING_MODEL") == model
    config = client.provider_configs[provider]
    config["api_key"] = "test"
    client.provider_configs = {provider: config}

    def handler(req):
        payload = json.loads(req.content)
        assert payload["model"] == model
        assert payload["reasoning_effort"] == "low"
        assert payload["include_reasoning"] is False
        assert payload["stream"] is False
        assert "reasoning_format" not in payload
        assert "chat_template_kwargs" not in payload
        assert "enable_thinking" not in payload
        assert "reasoning_budget" not in payload
        return httpx.Response(200, json=completion(model=model, reasoning="private", reasoning_content="private"))

    response = run(client, handler, request(model=model))
    assert response.provider_used == provider
    assert response.choices[0].message.content == "Hello"
    assert "private" not in response.model_dump_json() + caplog.text
    assert set(response.choices[0].message.model_dump()) == {"role", "content"}


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


@pytest.mark.parametrize("route,model_field", [
    ("groq_fast", "GROQ_FAST_MODEL"), ("groq_reasoning", "GROQ_REASONING_MODEL"),
    ("nvidia", "NVIDIA_DEFAULT_MODEL"), ("cerebras_primary", "CEREBRAS_PRIMARY_MODEL"),
])
@pytest.mark.parametrize("model", [
    "openai/gpt-oss-20b", "openai/gpt-oss-120b",
    "nvidia/nemotron-3.5-lightning-30b-a3b", "test/alternate-model",
])
@pytest.mark.parametrize("pinned", [False, True])
def test_provider_parameters_require_both_route_and_model(monkeypatch, route, model_field, model, pinned):
    # Cross-provider identifiers are deliberate configuration fixtures, not a
    # claim that these providers actually serve every model in the matrix.
    monkeypatch.setattr(settings, "PROVIDER_ORDER", [route])
    monkeypatch.setattr(settings, model_field, model)
    monkeypatch.setattr(settings, "NVIDIA_ENABLED", True)
    monkeypatch.setattr(settings, "CEREBRAS_ENABLED", True)
    monkeypatch.setattr(settings, "NVIDIA_ALLOWED_MODELS", [model])
    monkeypatch.setattr(settings, "NVIDIA_ENABLE_THINKING", True)
    circuit_breaker._states.clear()
    client = MultiProviderClient()
    client.provider_configs[route]["api_key"] = "test"

    def handler(req):
        payload = json.loads(req.content)
        expected_keys = {"model", "messages", "temperature", "max_tokens", "stream"}
        if route in {"groq_fast", "groq_reasoning"} and model in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
            expected_keys |= {"reasoning_effort", "include_reasoning"}
            assert payload["reasoning_effort"] == "low"
            assert payload["include_reasoning"] is False
        if route == "nvidia" and model == "nvidia/nemotron-3.5-lightning-30b-a3b":
            expected_keys |= {"chat_template_kwargs", "reasoning_budget"}
            assert payload["chat_template_kwargs"] == {"enable_thinking": True}
            assert payload["reasoning_budget"] == 64
        assert set(payload) == expected_keys
        assert payload["model"] == model
        assert payload["max_tokens"] == 128
        assert payload["stream"] is False
        return httpx.Response(200, json=completion(model=model))

    try:
        response = run(client, handler, request(max_tokens=128, **({"model": model} if pinned else {})))
        assert response.provider_used == route
        assert not response.fallback_used
    finally:
        circuit_breaker._states.clear()


@pytest.mark.parametrize("model", ["openai/gpt-oss-20b", "openai/gpt-oss-120b"])
@pytest.mark.parametrize("failure,category", [
    ("reasoning_only", "unusable_output"), ("mismatch", "model_mismatch"),
    ("malformed", "invalid_response"),
])
def test_suppressed_groq_reasoning_preserves_validation_and_fallback(client, model, failure, category, monkeypatch, caplog):
    monkeypatch.setattr(settings, "GROQ_FAST_MODEL", model)
    client.provider_configs["groq_fast"].update(default_model=model, api_key="test")
    client.provider_configs = {name: client.provider_configs[name] for name in ("groq_fast", "nvidia")}
    attempted = []

    def handler(req):
        payload = json.loads(req.content)
        attempted.append(payload["model"])
        if req.url.host == "api.groq.com":
            assert payload["include_reasoning"] is False
            assert payload["reasoning_effort"] == "low"
            assert "reasoning_format" not in payload
            body = completion(model=model, reasoning="private", reasoning_content="private")
            if failure == "reasoning_only":
                body["choices"][0]["message"]["content"] = None
            elif failure == "mismatch":
                body["model"] = "unexpected"
            else:
                body["choices"][0]["message"]["content"] = {"text": "Hello"}
            return httpx.Response(200, json=body)
        assert "include_reasoning" not in payload
        assert "reasoning_effort" not in payload
        return httpx.Response(200, json=completion(reasoning_content="private"))

    response = run(client, handler)
    assert attempted == [model, settings.NVIDIA_DEFAULT_MODEL]
    assert response.provider_used == "nvidia" and response.fallback_used
    assert response.routing_diagnostics[0].category == category
    assert "private" not in response.model_dump_json() + caplog.text
