import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

import app.services.provider_client as provider_module
from app.core.circuit_breaker import CircuitBreaker, ProviderStatus
from app.schemas.requests import ChatCompletionRequest
from app.services.provider_client import MultiProviderClient
from app.services.routing_errors import RoutingError
from config.settings import settings


def completion():
    return {
        "id": "completion-test",
        "created": 1,
        "model": settings.GROQ_FAST_MODEL,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "A usable answer"},
            "finish_reason": "stop",
        }],
    }


@pytest.fixture
def route(monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_ORDER", ["groq_fast", "nvidia", "groq_reasoning"])
    monkeypatch.setattr(settings, "NVIDIA_ENABLED", True)
    monkeypatch.setattr(settings, "NVIDIA_ALLOWED_MODELS", [settings.NVIDIA_DEFAULT_MODEL])
    monkeypatch.setattr(settings, "PROVIDER_MODEL_ALIASES", {})
    for field in ("GROQ_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.setattr(settings, field, "secret-test-key")
    breaker = CircuitBreaker(failure_threshold=3, recovery_time_seconds=10)
    monkeypatch.setattr(provider_module, "circuit_breaker", breaker)
    client = MultiProviderClient()
    client.mock_fallback_enabled = False
    return client, breaker


def run(client, handler, model=None):
    async def execute():
        client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.execute_chat_completion(ChatCompletionRequest(
                messages=[{"role": "user", "content": "Hello"}], model=model,
            ))
        finally:
            await client.close()

    return asyncio.run(execute())


def only_fast(client):
    client.provider_configs = {"groq_fast": client.provider_configs["groq_fast"]}


@pytest.mark.parametrize("content", [None, "", " ", "\n\t"])
@pytest.mark.parametrize("finish", ["stop", "length"])
def test_unusable_text_does_not_damage_health_and_fails_over(route, content, finish):
    client, breaker = route
    body = completion()
    body["choices"][0]["message"].update(content=content, reasoning_content="private reasoning")
    body["choices"][0]["finish_reason"] = finish
    body["usage"] = {
        "prompt_tokens": 10, "completion_tokens": 4096, "total_tokens": 4106,
        "completion_tokens_details": {"reasoning_tokens": 4096},
    }
    attempts = []

    def handler(req):
        attempts.append(json.loads(req.content)["model"])
        if len(attempts) == 1:
            return httpx.Response(200, json=body)
        usable = completion()
        usable["model"] = attempts[-1]
        return httpx.Response(200, json=usable)

    response = run(client, handler)
    assert attempts == [settings.GROQ_FAST_MODEL, settings.NVIDIA_DEFAULT_MODEL]
    assert response.provider_used == "nvidia"
    assert response.fallback_used is True
    first, selected = response.routing_diagnostics
    assert first.category == "unusable_output"
    assert first.attempted and not first.selected
    assert selected.attempted and selected.selected and selected.fallback_used
    assert not first.circuit_breaker_affected
    assert breaker.snapshot()["groq_fast"]["failures"] == 0
    assert "private reasoning" not in response.model_dump_json()


MALFORMED = [
    None, [], "not an object", 1,
    {}, {"choices": None}, {"choices": []}, {"choices": {}},
    {"choices": [None]}, {"choices": ["bad"]},
]


@pytest.mark.parametrize("body", MALFORMED)
def test_invalid_json_structure_is_a_reliability_failure(route, body):
    client, breaker = route
    only_fast(client)
    with pytest.raises(RoutingError) as error:
        run(client, lambda _: httpx.Response(200, json=body) if body is not None else httpx.Response(200, content=b"null"))
    assert error.value.diagnostics[0].category == "invalid_response"
    assert breaker.snapshot()["groq_fast"]["failures"] == 1


@pytest.mark.parametrize("change", [
    "no_message", "null_message", "list_message", "no_content", "object_content",
    "no_role", "user_role", "system_role", "no_finish", "null_finish", "empty_finish",
    "unknown_finish", "numeric_finish", "no_model", "null_model", "empty_model", "list_model",
])
def test_invalid_choice_and_model_structure(route, change):
    client, breaker = route
    only_fast(client)
    body = completion()
    choice = body["choices"][0]
    if change == "no_message":
        del choice["message"]
    elif change == "null_message":
        choice["message"] = None
    elif change == "list_message":
        choice["message"] = []
    elif change == "no_content":
        del choice["message"]["content"]
    elif change == "object_content":
        choice["message"]["content"] = {"text": "bad"}
    elif change == "no_role":
        del choice["message"]["role"]
    elif change.endswith("_role"):
        choice["message"]["role"] = change.removesuffix("_role")
    elif change == "no_finish":
        del choice["finish_reason"]
    elif change.endswith("_finish"):
        choice["finish_reason"] = {"null_finish": None, "empty_finish": "", "unknown_finish": "bad", "numeric_finish": 1}[change]
    elif change == "no_model":
        del body["model"]
    else:
        body["model"] = {"null_model": None, "empty_model": "", "list_model": []}[change]
    with pytest.raises(RoutingError) as error:
        run(client, lambda _: httpx.Response(200, json=body))
    assert error.value.diagnostics[0].category == "invalid_response"
    assert breaker.snapshot()["groq_fast"]["failures"] == 1


@pytest.mark.parametrize("usage", [
    {}, [], False, "bad",
    {"prompt_tokens": 1, "completion_tokens": 2},
    {"prompt_tokens": -1, "completion_tokens": 2, "total_tokens": 1},
    {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3},
    {"prompt_tokens": "1", "completion_tokens": 2, "total_tokens": 3},
    {"prompt_tokens": 1.0, "completion_tokens": 2, "total_tokens": 3},
    {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 10},
    {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "completion_tokens_details": []},
    {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "completion_tokens_details": {"reasoning_tokens": 3}},
    {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "completion_tokens_details": {"reasoning_tokens": -1}},
    {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "prompt_tokens_details": {"cached_tokens": True}},
])
def test_invalid_supplied_usage_is_not_silently_estimated(route, usage):
    client, breaker = route
    only_fast(client)
    body = completion()
    body["usage"] = usage
    with pytest.raises(RoutingError) as error:
        run(client, lambda _: httpx.Response(200, json=body))
    assert error.value.diagnostics[0].category == "invalid_response"
    assert breaker.snapshot()["groq_fast"]["failures"] == 1


@pytest.mark.parametrize("usage", [None, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}])
def test_optional_usage_and_zero_usage_are_accepted(route, usage):
    client, _ = route
    only_fast(client)
    body = completion()
    body["usage"] = usage
    response = run(client, lambda _: httpx.Response(200, json=body))
    assert response.usage.token_usage_is_estimated is (usage is None)
    assert response.fallback_used is False
    assert response.routing_diagnostics[0].selected


def test_absent_usage_metadata_is_accepted(route):
    client, _ = route
    only_fast(client)
    response = run(client, lambda _: httpx.Response(200, json=completion()))
    assert response.usage.token_usage_is_estimated


@pytest.mark.parametrize("reason", ["tool_calls", "function_call", "content_filter"])
def test_non_text_finish_reasons_are_unusable_not_provider_outages(route, reason):
    client, breaker = route
    only_fast(client)
    body = completion()
    body["choices"][0]["finish_reason"] = reason
    with pytest.raises(RoutingError) as error:
        run(client, lambda _: httpx.Response(200, json=body))
    assert error.value.diagnostics[0].category == "unusable_output"
    assert breaker.snapshot()["groq_fast"]["failures"] == 0


def test_length_finish_with_visible_text_is_usable(route):
    client, _ = route
    only_fast(client)
    body = completion()
    body["choices"][0]["finish_reason"] = "length"
    assert run(client, lambda _: httpx.Response(200, json=body)).choices[0].finish_reason == "length"


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("id", 12), ("created", "123"), ("created", True),
    ("created", -1), ("object", "chat.completion.chunk"),
])
def test_present_response_metadata_is_strictly_typed(route, field, value):
    client, _ = route
    only_fast(client)
    body = completion()
    body[field] = value
    with pytest.raises(RoutingError) as error:
        run(client, lambda _: httpx.Response(200, json=body))
    assert error.value.diagnostics[0].category == "invalid_response"


@pytest.mark.parametrize("index", [True, "0", -1, None])
def test_choice_index_is_not_coerced(route, index):
    client, _ = route
    only_fast(client)
    body = completion()
    body["choices"][0]["index"] = index
    with pytest.raises(RoutingError):
        run(client, lambda _: httpx.Response(200, json=body))


def test_optional_message_metadata_does_not_invalidate_text(route):
    client, _ = route
    only_fast(client)
    body = completion()
    body["choices"][0]["message"].update(reasoning_content="internal", refusal=None, annotations=[])
    response = run(client, lambda _: httpx.Response(200, json=body))
    assert response.choices[0].message.content == "A usable answer"
    assert "internal" not in response.model_dump_json()


def test_all_choices_are_validated(route):
    client, _ = route
    only_fast(client)
    body = completion()
    invalid = copy.deepcopy(body["choices"][0])
    invalid["message"]["content"] = None
    body["choices"].append(invalid)
    with pytest.raises(RoutingError):
        run(client, lambda _: httpx.Response(200, json=body))


def test_model_mismatch_cannot_substitute_for_explicit_model(route):
    client, breaker = route
    client.mock_fallback_enabled = True
    client.provider_configs["mock_fallback"] = {"is_mock": True}
    attempts = []
    body = completion()
    body["model"] = settings.NVIDIA_DEFAULT_MODEL

    def handler(req):
        attempts.append(json.loads(req.content)["model"])
        return httpx.Response(200, json=body)

    with pytest.raises(RoutingError) as error:
        run(client, handler, model=settings.GROQ_FAST_MODEL)
    assert attempts == [settings.GROQ_FAST_MODEL]
    assert error.value.diagnostics[0].category == "model_mismatch"
    assert not any(item.selected for item in error.value.diagnostics)
    assert breaker.snapshot()["groq_fast"]["failures"] == 0


def test_model_aliases_are_explicit_directional_and_provider_scoped(route, monkeypatch):
    client, breaker = route
    alias = "provider-model-version"
    monkeypatch.setattr(settings, "PROVIDER_MODEL_ALIASES", {"groq_fast": {settings.GROQ_FAST_MODEL: [alias]}})
    configured = MultiProviderClient()
    only_fast(configured)
    body = completion()
    body["model"] = alias
    response = run(configured, lambda _: httpx.Response(200, json=body), model=settings.GROQ_FAST_MODEL)
    assert response.model == alias
    only_fast(client)
    with pytest.raises(RoutingError) as error:
        run(client, lambda _: httpx.Response(200, json=body))
    assert error.value.diagnostics[0].category == "model_mismatch"
    assert breaker.snapshot()["groq_fast"]["failures"] == 0


@pytest.mark.parametrize("status,category,health", [
    (400, "invalid_request", False), (401, "auth_error", False), (403, "auth_error", False),
    (404, "unsupported_model", False), (408, "timeout", True), (422, "invalid_request", False),
    (429, "rate_limited", True), (500, "server_error", True), (502, "server_error", True), (503, "server_error", True),
])
def test_http_classification_and_breaker_thresholds(route, status, category, health):
    client, breaker = route
    only_fast(client)
    for _ in range(3):
        with pytest.raises(RoutingError) as error:
            run(client, lambda _: httpx.Response(status, text="secret raw body", headers={"Retry-After": "60"}))
        assert error.value.diagnostics[0].category in {category, "circuit_open"}
    state = breaker.snapshot()["groq_fast"]
    assert (state["status"] == ProviderStatus.UNHEALTHY) is health
    assert bool(state["failures"]) is health
    if status == 429:
        assert state["failures"] == 3
        assert not breaker.is_available("groq_fast")


@pytest.mark.parametrize("failure,category,health", [
    (httpx.ReadTimeout("secret"), "timeout", True),
    (httpx.ConnectError("secret"), "connection_error", True),
    (httpx.InvalidURL("secret"), "provider_configuration_error", False),
    (httpx.UnsupportedProtocol("secret"), "provider_configuration_error", False),
])
def test_transport_classification(route, failure, category, health):
    client, breaker = route
    only_fast(client)

    def handler(_):
        raise failure

    with pytest.raises(RoutingError) as error:
        run(client, handler)
    assert error.value.diagnostics[0].category == category
    assert bool(breaker.snapshot()["groq_fast"]["failures"]) is health


def test_malformed_json_opens_breaker_after_threshold(route):
    client, breaker = route
    only_fast(client)
    for _ in range(3):
        with pytest.raises(RoutingError) as error:
            run(client, lambda _: httpx.Response(200, content=b"secret-not-json"))
        assert error.value.diagnostics[0].category == "invalid_response"
    assert not breaker.is_available("groq_fast")


def test_circuit_skip_is_distinct_from_an_attempt_and_success_resets_health(route):
    client, breaker = route
    breaker.record_failure("groq_fast", force_open=True, admission=breaker.acquire("groq_fast"))
    breaker.record_failure("nvidia", admission=breaker.acquire("nvidia"))

    def handler(req):
        body = completion()
        body["model"] = json.loads(req.content)["model"]
        return httpx.Response(200, json=body)

    response = run(client, handler)
    skipped, selected = response.routing_diagnostics
    assert skipped.category == "circuit_open" and skipped.circuit_breaker_affected
    assert not skipped.attempted and not skipped.selected
    assert selected.provider == "nvidia" and selected.selected and selected.attempted
    assert response.fallback_used
    assert breaker.snapshot()["nvidia"]["failures"] == 0


@pytest.mark.parametrize("kind", ["auth", "unusable", "mismatch", "invalid_request"])
def test_neutral_recovery_probes_release_half_open_without_adding_failures(route, monkeypatch, kind):
    client, breaker = route
    only_fast(client)
    clock = [100.0]
    monkeypatch.setattr("app.core.circuit_breaker.time", SimpleNamespace(monotonic=lambda: clock[0]))
    breaker.record_failure("groq_fast", force_open=True, admission=breaker.acquire("groq_fast"))
    clock[0] += 10
    body = completion()
    if kind == "unusable":
        body["choices"][0]["message"]["content"] = None
    if kind == "mismatch":
        body["model"] = "wrong"
    status = {"auth": 401, "invalid_request": 400}.get(kind, 200)
    with pytest.raises(RoutingError):
        run(client, lambda _: httpx.Response(status, json=body))
    state = breaker.snapshot()["groq_fast"]
    assert state["failures"] == 3
    assert state["status"] == ProviderStatus.UNHEALTHY
    assert not state["probe_in_flight"]
    clock[0] += 10
    assert breaker.is_available("groq_fast")


def test_terminal_errors_are_sanitized_and_deadline_is_shared(route, monkeypatch, caplog):
    client, _ = route
    clock = [0.0]
    monkeypatch.setattr(provider_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(settings, "TOTAL_REQUEST_TIMEOUT_SECONDS", 10.0)
    budgets = []

    async def send(config, req, remaining):
        budgets.append(remaining)
        clock[0] += 6
        return False, 503, "server_error", True, None

    monkeypatch.setattr(client, "_send_http_request", send)
    with pytest.raises(RoutingError) as error:
        asyncio.run(client.execute_chat_completion(ChatCompletionRequest(messages=[{"role": "user", "content": "Hello"}])))
    assert budgets == [10.0, 4.0]
    public = error.value.public_error()
    assert public["code"] == "deadline_exceeded"
    assert [item["provider"] for item in public["routing_diagnostics"]] == ["groq_fast", "nvidia", "groq_reasoning"]
    assert public["routing_diagnostics"][-1]["category"] == "deadline_exceeded"
    assert not public["routing_diagnostics"][-1]["attempted"]
    assert "secret-test-key" not in json.dumps(public) + caplog.text + str(error.value)


def test_late_valid_response_is_not_selected_after_deadline(route, monkeypatch):
    client, breaker = route
    clock = [0.0]
    monkeypatch.setattr(provider_module, "time", SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 1.0))
    monkeypatch.setattr(settings, "TOTAL_REQUEST_TIMEOUT_SECONDS", 10.0)

    async def send(config, req, remaining):
        clock[0] = 11.0
        return True, completion(), "success", True, None

    monkeypatch.setattr(client, "_send_http_request", send)
    with pytest.raises(RoutingError) as error:
        asyncio.run(client.execute_chat_completion(ChatCompletionRequest(messages=[{"role": "user", "content": "Hello"}])))
    assert error.value.code == "deadline_exceeded"
    assert not any(item.selected for item in error.value.diagnostics)
    assert error.value.diagnostics[0].category == "deadline_exceeded"
    assert breaker.snapshot()["groq_fast"]["failures"] == 0


def test_terminal_error_contains_no_raw_body_or_credentials(route, caplog):
    client, _ = route
    with pytest.raises(RoutingError) as error:
        run(client, lambda _: httpx.Response(401, text="raw secret-test-key upstream details"))
    serialized = json.dumps(error.value.public_error()) + str(error.value) + caplog.text
    assert "raw secret-test-key upstream details" not in serialized
    assert "secret-test-key" not in serialized
    assert error.value.code == "no_provider_available"
    assert all(item.attempted and not item.selected for item in error.value.diagnostics)


def test_api_terminal_error_preserves_detail_and_adds_structured_diagnostics(route, monkeypatch):
    import main

    client, _ = route
    monkeypatch.setattr(main, "provider_client", client)
    monkeypatch.setattr(settings, "ROUTER_API_KEY", "router-test-key")

    async def execute():
        client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(403, text="secret-test-key")))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as api:
                return await api.post(
                    "/v1/chat/completions", headers={"X-API-Key": "router-test-key"},
                    json={"messages": [{"role": "user", "content": "Hello"}]},
                )
        finally:
            await client.close()

    response = asyncio.run(execute())
    assert response.status_code == 503
    assert response.json()["detail"] == "No AI provider is currently available."
    assert response.json()["error"]["code"] == "no_provider_available"
    assert [item["category"] for item in response.json()["error"]["routing_diagnostics"]] == ["auth_error"] * 3
    assert "secret-test-key" not in response.text


def test_older_inflight_success_cannot_clear_newer_rate_limit(route):
    client, breaker = route
    only_fast(client)

    async def execute():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def handler(req):
            calls.append(req)
            if len(calls) == 1:
                entered.set()
                await release.wait()
                return httpx.Response(200, json=completion())
            return httpx.Response(429, headers={"Retry-After": "60"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            client.http_client = transport
            req = ChatCompletionRequest(messages=[{"role": "user", "content": "Hello"}])
            older = asyncio.create_task(client.execute_chat_completion(req))
            try:
                await entered.wait()
                with pytest.raises(RoutingError):
                    await client.execute_chat_completion(req)
                release.set()
                assert (await older).provider_used == "groq_fast"
                assert not breaker.is_available("groq_fast")
                assert breaker.is_available("nvidia")
                with pytest.raises(RoutingError) as error:
                    await client.execute_chat_completion(req)
                assert error.value.diagnostics[0].category == "circuit_open"
                assert len(calls) == 2
            finally:
                release.set()
                await older
                client.http_client = None

    asyncio.run(execute())


@pytest.mark.parametrize("header,seconds", [
    (None, 10), ("invalid", 10), ("-1", 10), ("nan", 10), ("inf", 10),
    ("5", 10), ("20", 20), ("6000", 30), ("Wed, 01 Jan 2025 00:02:00 GMT", 30),
])
def test_retry_after_is_bounded_provider_scoped_and_respected(route, monkeypatch, header, seconds):
    from datetime import datetime, timezone

    client, breaker = route
    only_fast(client)
    clock = [100.0]
    monkeypatch.setattr("app.core.circuit_breaker.time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(provider_module, "datetime", SimpleNamespace(now=lambda _: datetime(2025, 1, 1, tzinfo=timezone.utc)))
    monkeypatch.setattr(settings, "MAX_UPSTREAM_RETRY_AFTER_SECONDS", 30.0)
    with pytest.raises(RoutingError):
        run(client, lambda _: httpx.Response(429, headers={"Retry-After": header} if header else {}))
    clock[0] = 100.0 + seconds - 0.01
    assert not breaker.is_available("groq_fast")
    assert breaker.is_available("nvidia")
    clock[0] = 100.0 + seconds
    assert breaker.is_available("groq_fast")


def test_oversized_usage_fails_over_and_releases_recovery_probe(route, monkeypatch):
    client, breaker = route
    clock = [100.0]
    monkeypatch.setattr("app.core.circuit_breaker.time", SimpleNamespace(monotonic=lambda: clock[0]))
    breaker.record_failure("groq_fast", force_open=True, admission=breaker.acquire("groq_fast"))
    clock[0] += 10
    attempts = []

    def handler(req):
        model = json.loads(req.content)["model"]
        attempts.append(model)
        body = completion()
        body["model"] = model
        if len(attempts) == 1:
            body["usage"] = {"prompt_tokens": 1, "completion_tokens": 10**400, "total_tokens": 10**400 + 1}
        return httpx.Response(200, json=body)

    response = run(client, handler)
    assert attempts == [settings.GROQ_FAST_MODEL, settings.NVIDIA_DEFAULT_MODEL]
    assert response.routing_diagnostics[0].category == "invalid_response"
    assert response.provider_used == "nvidia"
    assert not breaker.snapshot()["groq_fast"]["probe_in_flight"]
    clock[0] += 10
    assert breaker.is_available("groq_fast")


@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
def test_unexpected_exit_cannot_leave_recovery_probe_stuck(route, monkeypatch, error_type):
    client, breaker = route
    only_fast(client)
    clock = [100.0]
    monkeypatch.setattr("app.core.circuit_breaker.time", SimpleNamespace(monotonic=lambda: clock[0]))
    breaker.record_failure("groq_fast", force_open=True, admission=breaker.acquire("groq_fast"))
    clock[0] += 10

    def handler(_):
        raise error_type("sensitive internal text")

    with pytest.raises(error_type):
        run(client, handler)
    assert not breaker.snapshot()["groq_fast"]["probe_in_flight"]
    clock[0] += 10
    assert breaker.is_available("groq_fast")


def test_deeply_nested_json_is_classified_and_finite(route):
    client, _ = route
    attempts = []

    def handler(req):
        attempts.append(req)
        return httpx.Response(200, content=b"[" * 2000 + b"0" + b"]" * 2000)

    with pytest.raises(RoutingError) as error:
        run(client, handler)
    assert len(attempts) == 3
    assert [item.category for item in error.value.diagnostics] == ["invalid_response"] * 3
