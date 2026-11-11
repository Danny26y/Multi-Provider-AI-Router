import asyncio

import httpx
import pytest
from prometheus_client import REGISTRY

import main
from app.core import metrics
from app.core.circuit_breaker import CircuitBreaker
from app.schemas.requests import MAX_REQUEST_BODY_BYTES, ChatCompletionRequest
from app.services.dashboard_client import DashboardClient, DashboardConfig
from app.services.provider_client import MultiProviderClient
from app.services.routing_errors import RoutingError
from config.settings import Settings, settings


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0


def api(method, path, **kwargs):
    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(settings, "ROUTER_API_KEY", "router-test-secret")
    monkeypatch.setattr(settings, "METRICS_API_KEY", None)
    monkeypatch.setattr(main, "rate_limiter", main.ClientRequestLimiter(60, 2))
    monkeypatch.setattr("app.services.provider_client.circuit_breaker", CircuitBreaker())


def test_fallback_counts_terminal_selection_and_usage_once(isolated, monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_ORDER", ["groq_fast", "nvidia"])
    monkeypatch.setattr(settings, "NVIDIA_ENABLED", True)
    monkeypatch.setattr(settings, "GROQ_API_KEY", "dummy")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "dummy")
    client = MultiProviderClient()
    names = {
        "terminal": ("ai_router_completions_total", {"provider": "nvidia", "outcome": "success"}),
        "selection": ("ai_router_provider_selections_total", {"provider": "nvidia"}),
        "fallback": ("ai_router_fallback_selections_total", {"provider": "nvidia"}),
        "tokens": ("ai_router_tokens_total", {"provider": "nvidia", "kind": "total", "source": "reported"}),
        "failure": ("ai_router_provider_failures_total", {"provider": "groq_fast", "category": "server_error", "retryable": "true"}),
        "first_latency": ("ai_router_provider_request_duration_seconds_count", {"provider": "groq_fast"}),
        "second_latency": ("ai_router_provider_request_duration_seconds_count", {"provider": "nvidia"}),
    }
    before = {key: sample(name, **labels) for key, (name, labels) in names.items()}
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={
            "model": settings.NVIDIA_DEFAULT_MODEL,
            "choices": [{"message": {"role": "assistant", "content": "Answer"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        })

    async def execute():
        client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.execute_chat_completion(ChatCompletionRequest(messages=[{"role": "user", "content": "Hello"}]))
        finally:
            await client.close()

    assert asyncio.run(execute()).provider_used == "nvidia"
    assert len(calls) == 2
    for key, (name, labels) in names.items():
        assert sample(name, **labels) - before[key] == (5 if key == "tokens" else 1)


def test_terminal_failure_has_no_fake_provider_selection(isolated, monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_ORDER", [])
    before = sample("ai_router_completions_total", provider="none", outcome="failed")
    with pytest.raises(RoutingError):
        asyncio.run(MultiProviderClient().execute_chat_completion(ChatCompletionRequest(messages=[{"role": "user", "content": "Hi"}])) )
    assert sample("ai_router_completions_total", provider="none", outcome="failed") == before + 1


def test_mock_usage_is_not_reported_as_real_provider_tokens(isolated, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "testing")
    monkeypatch.setattr(settings, "ENABLE_MOCK_FALLBACK", True)
    monkeypatch.setattr(settings, "PROVIDER_ORDER", ["mock_fallback"])
    before = sample("ai_router_tokens_total", provider="mock_fallback", kind="total", source="reported")
    response = asyncio.run(MultiProviderClient().execute_chat_completion(ChatCompletionRequest(messages=[{"role": "user", "content": "Hi"}])) )
    assert response.provider_used == "mock_fallback"
    assert sample("ai_router_tokens_total", provider="mock_fallback", kind="total", source="reported") == before


def test_circuit_transition_counter_counts_changes_not_observations(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.core.circuit_breaker.time.monotonic", lambda: clock[0])
    breaker = CircuitBreaker(recovery_time_seconds=10)
    labels = {"provider": "groq_fast", "from_state": "healthy", "to_state": "unhealthy"}
    before = sample("ai_router_circuit_transitions_total", **labels)
    breaker.record_failure("groq_fast", force_open=True)
    breaker.record_failure("groq_fast", force_open=True)
    breaker.is_available("groq_fast")
    assert sample("ai_router_circuit_transitions_total", **labels) == before + 1
    clock[0] += 10
    assert breaker.is_available("groq_fast")
    breaker.record_success("groq_fast")
    assert sample("ai_router_circuit_state", provider="groq_fast") == 0


def test_unmatched_paths_and_unknown_methods_have_bounded_metric_labels(isolated):
    before = sample("ai_router_http_requests_total", method="GET", path="unmatched", status_code="404")
    for index in range(5):
        assert api("GET", f"/untrusted-secret-{index}").status_code == 404
    assert sample("ai_router_http_requests_total", method="GET", path="unmatched", status_code="404") == before + 5
    assert metrics.request_labels("CUSTOM-secret", "/untrusted") == ("OTHER", "unmatched")
    assert metrics.provider_label("arbitrary-model-name") == "other"
    text = metrics.render_metrics()[0].decode()
    assert "untrusted-secret" not in text
    assert sample("ai_router_http_inflight") == 0


@pytest.mark.parametrize("declared", [None, "1"])
def test_actual_chunked_body_limit_cannot_be_bypassed(isolated, declared):
    async def body():
        yield b"x" * (MAX_REQUEST_BODY_BYTES // 2)
        yield b"x" * (MAX_REQUEST_BODY_BYTES // 2 + 1)

    headers = {"X-API-Key": "router-test-secret"}
    if declared:
        headers["Content-Length"] = declared
    response = api("POST", "/v1/chat/completions", headers=headers, content=body())
    assert response.status_code == 413
    assert response.json()["detail"] == "Request body is too large."
    assert sample("ai_router_http_inflight") == 0


def test_body_read_timeout_is_bounded(isolated, monkeypatch):
    monkeypatch.setattr(settings, "REQUEST_BODY_TIMEOUT_SECONDS", 0.01)

    async def body():
        await asyncio.Event().wait()
        yield b"{}"

    assert api("POST", "/v1/chat/completions", content=body()).status_code == 408


def test_invalid_content_length_and_validation_errors_are_sanitized(isolated):
    for value in ("-1", "not-an-integer"):
        assert api("POST", "/v1/chat/completions", headers={"Content-Length": value}, content=b"{}").status_code == 400
    response = api("POST", "/v1/chat/completions", headers={"X-API-Key": "router-test-secret"}, json={"secret-prompt": "do-not-reflect"})
    assert response.status_code == 422
    assert "do-not-reflect" not in response.text and "secret-prompt" not in response.text
    assert isinstance(response.json()["detail"], list)
    assert "input" not in response.json()["detail"][0]


def test_metrics_can_require_a_separate_key_while_health_stays_public(isolated, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_API_KEY", "separate-metrics-secret")
    assert api("GET", "/metrics").status_code == 401
    assert api("GET", "/metrics", headers={"X-API-Key": "router-test-secret"}).status_code == 401
    assert api("GET", "/metrics", headers={"X-Metrics-Key": "separate-metrics-secret"}).status_code == 200
    assert api("GET", "/health/live").json() == {"status": "online"}


def test_dashboard_metrics_credential_is_scoped_and_never_displayed():
    config = DashboardConfig("https://router.test", "chat-secret", metrics_api_key="metrics-secret")

    def handler(req):
        assert req.url.path == "/metrics" and req.headers["X-Metrics-Key"] == "metrics-secret"
        assert "x-api-key" not in req.headers
        return httpx.Response(200, text='ai_router_provider_selections_total{provider="metrics-secret"} 1\n')

    result = DashboardClient(config, transport=httpx.MockTransport(handler)).metrics()
    assert "metrics-secret" not in str(result) + repr(config)


def test_dashboard_protected_metrics_error_identifies_the_correct_key():
    config = DashboardConfig("https://router.test", "chat-secret")
    result = DashboardClient(config, transport=httpx.MockTransport(lambda _: httpx.Response(401, text="raw-body"))).metrics()
    assert result["message"] == "Metrics access denied. Check the server-side dashboard metrics key."
    assert "raw-body" not in str(result)


def test_client_tracking_is_bounded_and_idle_clients_are_reclaimed(isolated, monkeypatch):
    monkeypatch.setattr(settings, "MAX_TRACKED_CLIENTS", 2)
    clock = [100.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    limiter = main.ClientRequestLimiter(60, 2)

    async def execute():
        assert await limiter.check_rate_limit("a") is None
        assert await limiter.check_rate_limit("b") is None
        assert await limiter.check_rate_limit("c") == 60
        assert len(limiter._timestamps) == 2
        clock[0] += 61
        assert await limiter.check_rate_limit("c") is None
        assert len(limiter._timestamps) == 1

    asyncio.run(execute())


def test_concurrency_queue_timeout_does_not_leak_slots_or_gauges(isolated, monkeypatch):
    monkeypatch.setattr(settings, "CONCURRENCY_QUEUE_TIMEOUT_SECONDS", 0.01)
    limiter = main.ClientRequestLimiter(60, 1)

    async def execute():
        await limiter.acquire()
        assert sample("ai_router_chat_inflight") == 1
        with pytest.raises(asyncio.TimeoutError):
            await limiter.acquire()
        assert sample("ai_router_chat_queued") == 0
        limiter.release()
        await limiter.acquire()
        limiter.release()
        assert sample("ai_router_chat_inflight") == 0

    asyncio.run(execute())


def test_correlation_ids_cannot_echo_server_credentials(isolated):
    response = api("GET", "/health/live", headers={"X-Request-ID": "router-test-secret"})
    assert response.headers["X-Request-ID"] != "router-test-secret"


@pytest.mark.parametrize("url", ["http://external.test", "https://user:secret@api.test", "https://api.test?key=secret"])
def test_provider_url_security_configuration(url):
    with pytest.raises(ValueError):
        Settings(GROQ_BASE_URL=url)


@pytest.mark.parametrize("estimated", [False, True])
def test_truncated_usage_is_labeled_by_reported_or_estimated_source(estimated):
    from app.schemas.requests import ChatCompletionResponse

    source = "estimated" if estimated else "reported"
    labels = {"provider": "groq_reasoning", "kind": "total", "source": source}
    before = sample("ai_router_tokens_total", **labels)
    terminal_before = sample("ai_router_completions_total", provider="groq_reasoning", outcome="truncated")
    response = ChatCompletionResponse(
        id="test", created=1, model="a-model-that-must-not-be-a-label", provider_used="groq_reasoning",
        choices=[{"message": {"role": "assistant", "content": "Partial answer"}, "finish_reason": "length"}],
        usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5, "token_usage_is_estimated": estimated},
    )
    metrics.record_completion(response)
    assert sample("ai_router_tokens_total", **labels) - before == 5
    assert sample("ai_router_completions_total", provider="groq_reasoning", outcome="truncated") == terminal_before + 1
    assert b"a-model-that-must-not-be-a-label" not in metrics.render_metrics()[0]


def test_cancelled_concurrency_waiter_restores_queue_gauge(isolated):
    limiter = main.ClientRequestLimiter(60, 1)

    async def execute():
        await limiter.acquire()
        pending = asyncio.create_task(limiter.acquire())
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert sample("ai_router_chat_queued") == 0
        assert sample("ai_router_chat_inflight") == 1
        limiter.release()
        assert sample("ai_router_chat_inflight") == 0

    asyncio.run(execute())


def test_busy_api_returns_bounded_capacity_error_and_recovers(isolated, monkeypatch):
    from app.schemas.requests import ChatCompletionResponse

    monkeypatch.setattr(settings, "CONCURRENCY_QUEUE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(main, "rate_limiter", main.ClientRequestLimiter(60, 1))
    before = sample("ai_router_limit_rejections_total", limit="concurrency")

    async def execute():
        entered, release = asyncio.Event(), asyncio.Event()

        async def complete(_request):
            entered.set()
            await release.wait()
            return ChatCompletionResponse(
                id="test", created=1, model="test", provider_used="nvidia",
                choices=[{"message": {"role": "assistant", "content": "Answer"}, "finish_reason": "stop"}],
            )

        monkeypatch.setattr(main.provider_client, "execute_chat_completion", complete)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            kwargs = {"headers": {"X-API-Key": "router-test-secret"}, "json": {"messages": [{"role": "user", "content": "Hi"}]}}
            first = asyncio.create_task(client.post("/v1/chat/completions", **kwargs))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                busy = await client.post("/v1/chat/completions", **kwargs)
                assert busy.status_code == 503 and busy.headers["Retry-After"] == "1"
                assert busy.json()["detail"] == "Request capacity reached. Retry shortly."
            finally:
                release.set()
                assert (await first).status_code == 200
        assert sample("ai_router_chat_inflight") == 0
        assert sample("ai_router_chat_queued") == 0

    asyncio.run(execute())
    assert sample("ai_router_limit_rejections_total", limit="concurrency") == before + 1


def test_upstream_body_cap_closes_stream_and_classifies_reliability_failure(monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPSTREAM_RESPONSE_BYTES", 16)
    closed = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 32

        async def aclose(self):
            closed.append(True)

    client = MultiProviderClient()

    async def execute():
        client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Body())))
        try:
            return await client._send_http_request(
                {"base_url": "https://upstream.test", "api_key": "dummy", "default_model": "test"},
                ChatCompletionRequest(messages=[{"role": "user", "content": "Hi"}]), 1,
            )
        finally:
            await client.close()

    result = asyncio.run(execute())
    assert result[0] is False and result[2:4] == ("invalid_response", True)
    assert closed == [True]


def test_upstream_error_body_is_not_read_and_redirect_is_not_followed():
    calls = []

    class ErrorBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("Error bodies must not be read")
            yield b"sensitive body"

    def handler(req):
        calls.append(req)
        return httpx.Response(307, headers={"Location": "https://attacker.test"}, stream=ErrorBody())

    client = MultiProviderClient()

    async def execute():
        # Even an injected client with redirects enabled must not forward provider credentials.
        client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        try:
            return await client._send_http_request(
                {"base_url": "https://upstream.test", "api_key": "dummy", "default_model": "test"},
                ChatCompletionRequest(messages=[{"role": "user", "content": "Hi"}]), 1,
            )
        finally:
            await client.close()

    assert asyncio.run(execute())[2] == "invalid_request"
    assert len(calls) == 1 and calls[0].url.host == "upstream.test"


def test_compressed_upstream_json_is_decoded_once():
    import gzip

    client = MultiProviderClient()

    async def execute():
        client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, content=gzip.compress(b'{"ok": true}'),
        )))
        try:
            return await client._send_http_request(
                {"base_url": "https://upstream.test", "api_key": "dummy", "default_model": "test"},
                ChatCompletionRequest(messages=[{"role": "user", "content": "Hi"}]), 1,
            )
        finally:
            await client.close()

    result = asyncio.run(execute())
    assert result[0] is True and result[1] == {"ok": True}


def test_readiness_requires_an_eligible_configured_route(isolated):
    client = MultiProviderClient()
    client.mock_fallback_enabled = False
    client.provider_configs = {"nvidia": {"api_key": "dummy", "default_model": "test", "allowed_models": []}}
    assert not client.is_ready()
    client.provider_configs["nvidia"]["allowed_models"] = ["alternate"]
    assert client.is_ready()
    client.provider_configs = {}
    client.mock_fallback_enabled = True
    assert not client.is_ready()
    client.provider_configs = {"mock_fallback": {"is_mock": True}}
    assert client.is_ready()
