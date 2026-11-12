"""Exercise the demo upstream through the real router and benchmark mapping."""

import asyncio

import httpx
import pytest

from app.core.circuit_breaker import CircuitBreaker
from app.schemas.requests import ChatCompletionRequest
from app.services.provider_client import MultiProviderClient
from app.services.routing_errors import RoutingError
from benchmarks.mock_upstream import app
from benchmarks.observations import completion_observation
from config.settings import settings
from scripts.demo import environment


@pytest.mark.parametrize("scenario,provider,category", [
    ("success", "groq_fast", None), ("fallback", "nvidia", "server_error"),
    ("reasoning", "nvidia", "unusable_output"), ("truncated", "groq_fast", None),
    ("failure", None, "server_error"),
])
def test_demo_scenarios_use_real_routing_and_semantic_validation(monkeypatch, scenario, provider, category):
    env = environment(scenario, 8000, 9000)
    for name in ("GROQ_BASE_URL", "NVIDIA_BASE_URL", "GROQ_API_KEY", "NVIDIA_API_KEY", "GROQ_FAST_MODEL",
                 "NVIDIA_DEFAULT_MODEL"):
        monkeypatch.setattr(settings, name, env[name])
    monkeypatch.setattr(settings, "NVIDIA_ALLOWED_MODELS", ["demo-nvidia"])
    monkeypatch.setattr(settings, "NVIDIA_ENABLED", True)
    monkeypatch.setattr(settings, "PROVIDER_ORDER", ["groq_fast", "nvidia"])
    monkeypatch.setattr("app.services.provider_client.circuit_breaker", CircuitBreaker())
    client = MultiProviderClient()

    async def execute():
        client.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        try:
            return await client.execute_chat_completion(ChatCompletionRequest(messages=[{"role": "user", "content": "Hi"}]))
        finally:
            await client.close()

    if provider is None:
        with pytest.raises(RoutingError) as error:
            asyncio.run(execute())
        assert all(item.category == category for item in error.value.diagnostics)
        assert "simulated-upstream-private-body" not in str(error.value.public_error())
    else:
        response = asyncio.run(execute())
        assert response.provider_used == provider
        observed = completion_observation(response.model_dump())
        assert observed == {"provider": provider, "fallback": provider == "nvidia", "truncated": scenario == "truncated"}
        if category:
            assert response.routing_diagnostics[0].category == category


@pytest.mark.parametrize("data", [None, [], {}, {"provider_used": "untrusted"}, {"provider_used": []}, {
    "provider_used": "nvidia", "fallback_used": False,
    "choices": [{"message": {"role": "assistant", "content": None}, "finish_reason": "length"}],
}])
def test_benchmark_does_not_count_unusable_json_as_success(data):
    assert completion_observation(data) is None


def test_demo_overrides_remote_settings_without_using_real_keys(monkeypatch):
    monkeypatch.setenv("GROQ_BASE_URL", "https://remote.invalid")
    monkeypatch.setenv("GROQ_API_KEY", "must-not-be-used")
    env = environment("fallback", 8123, 9123)
    assert env["GROQ_BASE_URL"] == "http://127.0.0.1:9123/outage/v1"
    assert env["NVIDIA_BASE_URL"] == "http://127.0.0.1:9123/success/v1"
    assert env["GROQ_API_KEY"] == env["NVIDIA_API_KEY"] == "local-demo-placeholder"
    assert env["ENABLE_MOCK_FALLBACK"] == "false"
