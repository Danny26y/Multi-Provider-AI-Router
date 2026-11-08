import json

import httpx
import pytest

from app.services.dashboard_client import (
    DashboardClient,
    DashboardConfig,
    DashboardConfigurationError,
    completion_view,
    diagnostics_view,
    metrics_view,
    session_summary,
)


KEY = "test-dashboard-secret"


def completion(**overrides):
    return {
        "choices": [{"message": {"role": "assistant", "content": "A routed answer"}, "finish_reason": "stop"}],
        "provider_used": "nvidia", "model": "nvidia/test-model", "fallback_used": True,
        "latency_seconds": 0.25,
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5,
                  "token_usage_is_estimated": False, "estimated_cost_usd": 0.00001, "cost_is_estimated": True},
        "routing_diagnostics": [{"provider": "groq_fast", "outcome": "failed", "category": "timeout", "attempted": True},
                                {"provider": "nvidia", "outcome": "success", "selected": True}],
        **overrides,
    }


def client(handler, base_url="https://router.example.test/prefix", key=KEY):
    return DashboardClient(DashboardConfig(base_url, key), transport=httpx.MockTransport(handler))


def test_router_url_payload_headers_and_timeout():
    requests = []

    def handler(req):
        requests.append(req)
        assert str(req.url) == "https://router.example.test/prefix/v1/chat/completions"
        assert req.headers["X-API-Key"] == KEY
        assert "authorization" not in req.headers
        assert all(0 < value <= 30 for value in req.extensions["timeout"].values())
        assert json.loads(req.content) == {
            "messages": [{"role": "user", "content": "Hello"}], "model": "explicit/model",
            "temperature": 0.2, "max_tokens": 2048, "stream": False,
        }
        return httpx.Response(200, json=completion())

    result = client(handler).prompt("Hello", model="explicit/model", temperature=0.2, max_tokens=2048)
    assert len(requests) == 1
    assert result["outcome"] == "completed"
    assert result["content"] == "A routed answer"
    assert result["provider"] == "nvidia"
    assert result["model"] == "nvidia/test-model"
    assert result["provider_latency_seconds"] == 0.25
    assert result["round_trip_seconds"] >= 0
    assert result["usage"]["total_tokens"] == 5
    assert result["usage"]["token_usage_is_estimated"] is False
    assert result["fallback_used"] is True
    assert result["diagnostics"][0]["category"] == "timeout"


def test_omitted_model_and_token_budget_preserve_router_defaults():
    def handler(req):
        payload = json.loads(req.content)
        assert "model" not in payload and "max_tokens" not in payload
        return httpx.Response(200, json=completion())

    assert client(handler).prompt("Hello")["outcome"] == "completed"


def test_missing_key_prevents_http_request():
    def forbidden(_):
        pytest.fail("Missing dashboard key must not reach network")

    result = client(forbidden, key="").prompt("Hello")
    assert result["outcome"] == "failed"
    assert "server-side" in result["message"]


@pytest.mark.parametrize("status,phrase", [(401, "authentication"), (403, "denied"), (422, "allowlist"), (429, "rate limit"), (503, "could not serve"), (500, "failed")])
def test_http_errors_never_present_raw_bodies(status, phrase, caplog):
    result = client(lambda _: httpx.Response(status, text=f"Authorization: Bearer {KEY} raw-upstream-trace")).prompt("Hello")
    assert result["outcome"] == "failed"
    assert phrase in result["message"]
    assert KEY not in json.dumps(result) + caplog.text
    assert "raw-upstream-trace" not in json.dumps(result) + caplog.text


@pytest.mark.parametrize("error,phrase", [(httpx.ReadTimeout(f"Authorization {KEY}"), "timed out"), (httpx.ConnectError(KEY), "connect"), (httpx.RemoteProtocolError(KEY), "connect")])
def test_transport_errors_are_sanitized(error, phrase, caplog):
    def handler(_):
        raise error

    result = client(handler).prompt("Hello")
    assert result["outcome"] == "failed"
    assert phrase in result["message"]
    assert KEY not in json.dumps(result) + caplog.text


def test_redirects_cannot_forward_key_to_another_host():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(307, headers={"Location": "https://attacker.test/steal"})

    assert client(handler).prompt("Hello")["outcome"] == "failed"
    assert len(calls) == 1
    assert calls[0].url.host == "router.example.test"


def test_configuration_from_environment_or_streamlit_secrets():
    config = DashboardConfig.from_sources({}, {"DASHBOARD_ROUTER_URL": "https://router.test/", "DASHBOARD_API_KEY": KEY})
    assert config.base_url == "https://router.test"
    assert config.api_key == KEY
    env = {"DASHBOARD_ROUTER_URL": "https://other-router.test", "ROUTER_API_KEY": KEY, "DASHBOARD_REQUEST_TIMEOUT_SECONDS": "42"}
    config = DashboardConfig.from_sources(env, {"DASHBOARD_ROUTER_URL": "https://ignored.test"})
    assert config.base_url == "https://other-router.test" and config.timeout_seconds == 42
    assert KEY not in repr(config)


@pytest.mark.parametrize("url", [
    "", "localhost:8000", "ftp://router.test", "http://external.test",
    f"https://user:{KEY}@router.test", f"https://router.test?api_key={KEY}",
    f"https://router.test/#{KEY}", f"https://router.test/{KEY}",
    "https://integrate.api.nvidia.com/v1", "https://api.groq.com/openai/v1", "https://api.cerebras.ai/v1",
])
def test_unsafe_urls_and_direct_provider_targets_are_rejected(url):
    with pytest.raises(DashboardConfigurationError) as error:
        DashboardConfig(url, KEY)
    assert KEY not in str(error.value)
    assert url not in str(error.value) or not url


@pytest.mark.parametrize("seconds", [0, -1, 121, float("inf"), float("nan")])
def test_timeouts_must_be_finite_and_bounded(seconds):
    with pytest.raises(DashboardConfigurationError):
        DashboardConfig("https://router.test", KEY, seconds)


@pytest.mark.parametrize("body", [{}, {"circuit_breaker_states": {"secret": KEY}}, [], None, {"status": KEY}])
def test_health_missing_fields_are_unknown_and_do_not_invent_circuits(body):
    paths = []

    def handler(req):
        paths.append(req.url.path)
        assert "x-api-key" not in req.headers
        return httpx.Response(200, json=body)

    result = client(handler, base_url="https://router.test").health()
    assert paths == ["/health", "/health/live", "/health/ready"]
    assert all(item["status"] == "unknown" for item in result.values())
    assert KEY not in json.dumps(result)


def test_actual_health_contract_and_unready_response():
    def handler(req):
        if req.url.path.endswith("ready"):
            return httpx.Response(503, json={"detail": "Service is not ready."})
        return httpx.Response(200, json={"status": "online"})

    health = client(handler, base_url="https://router.test").health()
    assert health["/health/live"]["status"] == "online"
    assert health["/health/ready"]["outcome"] == "failed"


def test_missing_response_values_are_not_zero_or_fake_provider_names():
    result = completion_view(completion(provider_used=None, model=None, usage=None, latency_seconds=None, fallback_used=None))
    assert result["provider"] is None and result["model"] is None
    assert result["provider_latency_seconds"] is None
    assert result["fallback_used"] is None
    assert all(value is None for value in result["usage"].values())


def test_truncation_preserves_text_and_finish_reason():
    body = completion()
    body["choices"][0]["finish_reason"] = "length"
    result = completion_view(body)
    assert result["outcome"] == "truncated"
    assert result["finish_reason"] == "length" and result["content"] == "A routed answer"


@pytest.mark.parametrize("body", [None, [], {}, {"choices": []}, {"choices": [{"message": {"role": "assistant", "content": None}}]}])
def test_invalid_success_bodies_are_safe_failures(body):
    result = completion_view(body)
    assert result["outcome"] == "failed"


def test_terminal_routing_diagnostics_are_whitelisted_and_redacted():
    body = {"error": {"code": "deadline_exceeded", "message": KEY, "routing_diagnostics": [
        {"provider": KEY, "outcome": "failed", "category": "timeout", "attempted": True,
         "Authorization": f"Bearer {KEY}", "raw_body": "sensitive-body"},
    ]}}
    result = client(lambda _: httpx.Response(503, json=body)).prompt("Hello")
    assert result["error_code"] == "deadline_exceeded"
    assert result["diagnostics"][0]["category"] == "timeout"
    assert KEY not in json.dumps(result) and "sensitive-body" not in json.dumps(result)
    assert diagnostics_view([{"category": {"bad": KEY}, "outcome": []}])[0]["category"] is None


def test_echoed_key_in_success_is_redacted_before_any_display():
    body = completion(provider_used=KEY, model=KEY)
    body["choices"][0]["message"]["content"] = f"echo {KEY}"
    result = client(lambda _: httpx.Response(200, json=body)).prompt("Hello")
    assert KEY not in json.dumps(result)


def test_empty_history_has_no_invented_latency_or_provider_data():
    summary = session_summary([])
    assert summary["requests"] == 0
    assert summary["mean_round_trip_seconds"] is None
    assert summary["provider_selections"] == {}
    assert summary["reported_fallbacks"] == 0


def test_session_aggregates_only_observed_values():
    summary = session_summary([
        {"outcome": "completed", "provider": "nvidia", "fallback_used": True, "round_trip_seconds": 1.0},
        {"outcome": "truncated", "provider": "groq_fast", "round_trip_seconds": 3.0},
        {"outcome": "failed", "provider": None},
    ])
    assert summary["requests"] == 3 and summary["failed"] == 1 and summary["truncated"] == 1
    assert summary["mean_round_trip_seconds"] == 2
    assert summary["reported_fallbacks"] == 1 and summary["fallback_flags_reported"] == 1
    assert summary["provider_selections"] == {"nvidia": 1, "groq_fast": 1}


def test_metrics_are_filtered_from_actual_prometheus_text():
    text = f'''# TYPE ai_router_http_requests_total counter
ai_router_http_requests_total{{method="POST",path="/v1/chat/completions",status_code="200"}} 5
# TYPE ai_router_circuit_state gauge
ai_router_circuit_state{{provider="groq_fast"}} 2
process_resident_memory_bytes 1234
ai_router_provider_failures_total{{provider="{KEY}",category="timeout",authorization="{KEY}"}} 1
'''
    rows = metrics_view(text, KEY)
    assert len(rows) == 3
    assert rows[0]["value"] == 5
    assert rows[1]["provider"] == "groq_fast" and rows[1]["value"] == 2
    assert KEY not in json.dumps(rows) and "process_resident_memory" not in json.dumps(rows)
    assert "authorization" not in json.dumps(rows)
    assert metrics_view("") == []
    assert metrics_view("not valid prometheus {{{") == []


def test_all_network_requests_target_only_the_configured_router():
    urls = []

    def handler(req):
        urls.append(req.url)
        if req.url.path == "/metrics":
            return httpx.Response(200, text="")
        if req.url.path.startswith("/health"):
            return httpx.Response(200, json={"status": "ready" if req.url.path.endswith("ready") else "online"})
        return httpx.Response(200, json=completion())

    router = client(handler, base_url="https://configured-router.test")
    router.health()
    router.metrics()
    router.prompt("Hello")
    assert len(urls) == 5
    assert {url.host for url in urls} == {"configured-router.test"}


def test_invalid_prompt_parameters_do_not_send_requests():
    def forbidden(_):
        pytest.fail("Invalid input reached networking")

    router = client(forbidden)
    assert router.prompt(" ")["outcome"] == "failed"
    assert router.prompt("Hello", temperature=3)["outcome"] == "failed"
    assert router.prompt("Hello", max_tokens=0)["outcome"] == "failed"


def test_dashboard_client_matches_real_fastapi_auth_and_completion_contract(monkeypatch):
    import asyncio

    import main
    from app.core.circuit_breaker import CircuitBreaker
    from app.services.provider_client import MultiProviderClient
    from config.settings import settings

    monkeypatch.setattr(settings, "ROUTER_API_KEY", KEY)
    monkeypatch.setattr(settings, "GROQ_API_KEY", "dummy-provider-key")
    monkeypatch.setattr(settings, "PROVIDER_ORDER", ["groq_fast"])
    monkeypatch.setattr("app.services.provider_client.circuit_breaker", CircuitBreaker())
    monkeypatch.setattr(main, "provider_client", MultiProviderClient())
    monkeypatch.setattr(main, "rate_limiter", main.ClientRequestLimiter(60, 20))
    upstream_calls = []

    def upstream(req):
        upstream_calls.append(req)
        body = completion(model=settings.GROQ_FAST_MODEL)
        return httpx.Response(200, json=body)

    def bridge(req):
        async def execute():
            main.provider_client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://router.test") as api:
                    response = await api.request(req.method, req.url.path, headers=req.headers, content=req.content)
                    return httpx.Response(response.status_code, headers=response.headers, content=response.content)
            finally:
                await main.provider_client.close()

        return asyncio.run(execute())

    router = client(bridge, base_url="https://router.test")
    result = router.prompt("Hello")
    assert result["outcome"] == "completed" and result["provider"] == "groq_fast"
    assert len(upstream_calls) == 1
    assert router.health()["/health/ready"]["status"] == "ready"
    assert router.metrics()["metrics"]
    denied = client(bridge, base_url="https://router.test", key="wrong-key").prompt("Hello")
    assert denied["outcome"] == "failed" and denied["http_status"] == 401
    assert len(upstream_calls) == 1
