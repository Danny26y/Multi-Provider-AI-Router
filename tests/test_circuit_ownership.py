"""Admission ownership under overlapping requests and recovery generations."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from app.core.circuit_breaker import CircuitBreaker, ProviderStatus
from app.schemas.requests import ChatCompletionRequest
from app.services.provider_client import MultiProviderClient
from app.services.routing_errors import RoutingError
from config.settings import settings


@pytest.fixture
def controlled(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.core.circuit_breaker.time", SimpleNamespace(monotonic=lambda: clock[0]))
    return CircuitBreaker(), clock


def open_circuit(breaker, retry_after=30):
    admission = breaker.acquire("groq_fast")
    assert admission is not None
    assert breaker.record_failure("groq_fast", force_open=True, retry_after_seconds=retry_after, admission=admission)


def state(breaker):
    return breaker.snapshot()["groq_fast"]


def test_exact_stale_success_cannot_close_newer_probe_or_hide_its_failure(controlled):
    breaker, clock = controlled
    older = breaker.acquire("groq_fast")
    for _ in range(3):
        assert breaker.record_failure("groq_fast", admission=breaker.acquire("groq_fast"))
    clock[0] = 31
    probe = breaker.acquire("groq_fast")
    assert probe is not None and state(breaker)["probe_in_flight"]
    assert not breaker.record_success("groq_fast", admission=older)
    assert state(breaker)["status"] == ProviderStatus.DEGRADED
    assert breaker.record_failure("groq_fast", admission=probe)
    assert state(breaker)["status"] == ProviderStatus.UNHEALTHY
    assert not breaker.is_available("groq_fast")
    clock[0] = 61
    recovery = breaker.acquire("groq_fast")
    assert recovery is not None
    assert breaker.record_success("groq_fast", admission=recovery)
    assert state(breaker)["status"] == ProviderStatus.HEALTHY


@pytest.mark.parametrize("phase", ["open", "probe", "recovered"])
@pytest.mark.parametrize("callback", ["success", "short_failure", "long_failure", "neutral"])
def test_stale_callbacks_cannot_override_a_newer_epoch(controlled, phase, callback):
    breaker, clock = controlled
    older = breaker.acquire("groq_fast")
    open_circuit(breaker, retry_after=60)
    clock[0] = 1
    if phase != "open":
        clock[0] = 60
        probe = breaker.acquire("groq_fast")
        assert probe is not None
        if phase == "recovered":
            assert breaker.record_success("groq_fast", admission=probe)
    before = state(breaker)
    if callback.endswith("failure"):
        assert not breaker.record_failure(
            "groq_fast", admission=older, force_open=True,
            retry_after_seconds=1 if callback == "short_failure" else 999,
        )
    else:
        assert not getattr(breaker, f"record_{callback}")("groq_fast", admission=older)
    assert state(breaker) == before
    if phase == "open":
        clock[0] = 59.9
        assert breaker.acquire("groq_fast") is None
        clock[0] = 60
        assert breaker.acquire("groq_fast") is not None
    elif phase == "probe":
        assert breaker.acquire("groq_fast") is None
        assert breaker.record_success("groq_fast", admission=probe)
    else:
        assert breaker.is_available("groq_fast")


@pytest.mark.parametrize("first", ["success", "failure", "neutral"])
def test_admissions_are_single_use_even_without_a_state_transition(controlled, first):
    breaker, _ = controlled
    admission = breaker.acquire("groq_fast")
    assert getattr(breaker, f"record_{first}")("groq_fast", admission=admission)
    before = state(breaker)
    for callback in ("success", "failure", "neutral"):
        assert not getattr(breaker, f"record_{callback}")("groq_fast", admission=admission)
    assert state(breaker) == before


def test_tokens_are_bound_to_breaker_provider_and_identity(controlled):
    breaker, _ = controlled
    admission = breaker.acquire("groq_fast")
    other_breaker = CircuitBreaker()
    assert not other_breaker.record_success("groq_fast", admission=admission)
    assert other_breaker.snapshot() == {}
    assert not breaker.record_success("nvidia", admission=admission)
    assert "nvidia" not in breaker.snapshot()
    assert not breaker.record_success("groq_fast", admission=replace(admission))
    assert breaker.record_success("groq_fast", admission=admission)


def test_overlapping_healthy_requests_still_count_real_failures_and_success(controlled):
    breaker, _ = controlled
    first, second, third = (breaker.acquire("groq_fast") for _ in range(3))
    assert breaker.record_failure("groq_fast", admission=first)
    assert state(breaker)["failures"] == 1
    assert breaker.record_success("groq_fast", admission=second)
    assert state(breaker)["failures"] == 0
    assert breaker.record_failure("groq_fast", admission=third)
    assert state(breaker)["failures"] == 1


def test_availability_queries_do_not_claim_or_release_a_probe(controlled):
    breaker, clock = controlled
    open_circuit(breaker)
    clock[0] = 30
    assert breaker.is_available("groq_fast") and breaker.is_available("groq_fast")
    assert not state(breaker)["probe_in_flight"]
    probe = breaker.acquire("groq_fast")
    assert probe is not None and not breaker.is_available("groq_fast")
    for callback in ("success", "failure", "neutral"):
        assert not getattr(breaker, f"record_{callback}")("groq_fast")
    assert state(breaker)["probe_in_flight"]
    assert breaker.record_neutral("groq_fast", admission=probe)
    clock[0] = 60
    newer = breaker.acquire("groq_fast")
    assert newer is not None
    assert not breaker.record_neutral("groq_fast", admission=probe)
    assert state(breaker)["probe_in_flight"]
    assert breaker.record_success("groq_fast", admission=newer)


def test_threaded_old_callbacks_cannot_take_the_single_probe(controlled):
    breaker, clock = controlled
    older = [breaker.acquire("groq_fast") for _ in range(16)]
    open_circuit(breaker)
    clock[0] = 30
    with ThreadPoolExecutor(max_workers=8) as executor:
        probes = list(executor.map(lambda _: breaker.acquire("groq_fast"), range(8)))
        active = [probe for probe in probes if probe is not None]
        assert len(active) == 1
        results = list(executor.map(lambda token: breaker.record_neutral("groq_fast", admission=token), older))
    assert results == [False] * 16
    assert state(breaker)["probe_in_flight"]
    assert breaker.record_failure("groq_fast", admission=active[0])
    assert not breaker.is_available("groq_fast")


def usable():
    return {"model": settings.GROQ_FAST_MODEL, "choices": [{
        "message": {"role": "assistant", "content": "Answer"}, "finish_reason": "stop",
    }]}


@pytest.mark.parametrize("older_outcome", ["success", "failure", "unusable", "cancel", "exception"])
@pytest.mark.parametrize("probe_outcome", ["success", "failure"])
def test_provider_callbacks_keep_new_probe_owned(controlled, monkeypatch, older_outcome, probe_outcome):
    breaker, clock = controlled
    monkeypatch.setattr("app.services.provider_client.circuit_breaker", breaker)
    monkeypatch.setattr(settings, "PROVIDER_ORDER", ["groq_fast"])
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-placeholder")
    monkeypatch.setattr(settings, "TOTAL_REQUEST_TIMEOUT_SECONDS", 120)
    client = MultiProviderClient()

    async def execute():
        entered, release, probe_entered, probe_release = (asyncio.Event() for _ in range(4))
        calls = []
        tasks = []

        async def handler(_request):
            calls.append(True)
            if len(calls) == 1:
                entered.set()
                await release.wait()
                if older_outcome == "exception":
                    raise RuntimeError("simulated failure")
                if older_outcome == "failure":
                    return httpx.Response(503)
                body = usable()
                if older_outcome == "unusable":
                    body["choices"][0]["message"]["content"] = None
                return httpx.Response(200, json=body)
            if len(calls) == 2:
                return httpx.Response(429, headers={"Retry-After": "60"})
            probe_entered.set()
            await probe_release.wait()
            return httpx.Response(200, json=usable()) if probe_outcome == "success" else httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            client.http_client = transport
            request = ChatCompletionRequest(messages=[{"role": "user", "content": "Hi"}])
            older = asyncio.create_task(client.execute_chat_completion(request))
            tasks.append(older)
            try:
                await asyncio.wait_for(entered.wait(), 2)
                with pytest.raises(RoutingError):
                    await client.execute_chat_completion(request)
                clock[0] = 61
                probe = asyncio.create_task(client.execute_chat_completion(request))
                tasks.append(probe)
                await asyncio.wait_for(probe_entered.wait(), 2)
                if older_outcome == "cancel":
                    older.cancel()
                release.set()
                if older_outcome == "success":
                    assert (await older).provider_used == "groq_fast"
                else:
                    error = asyncio.CancelledError if older_outcome == "cancel" else (
                        RuntimeError if older_outcome == "exception" else RoutingError
                    )
                    with pytest.raises(error):
                        await older
                assert state(breaker)["status"] == ProviderStatus.DEGRADED
                assert state(breaker)["probe_in_flight"]
                probe_release.set()
                if probe_outcome == "success":
                    assert (await probe).provider_used == "groq_fast"
                    assert state(breaker)["status"] == ProviderStatus.HEALTHY
                else:
                    with pytest.raises(RoutingError):
                        await probe
                    assert state(breaker)["status"] == ProviderStatus.UNHEALTHY
                assert not state(breaker)["probe_in_flight"]
                assert len(calls) == 3
            finally:
                release.set()
                probe_release.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                client.http_client = None

    asyncio.run(execute())


def test_cancelling_current_provider_probe_releases_its_ownership(controlled, monkeypatch):
    breaker, clock = controlled
    open_circuit(breaker)
    clock[0] = 30
    monkeypatch.setattr("app.services.provider_client.circuit_breaker", breaker)
    monkeypatch.setattr(settings, "PROVIDER_ORDER", ["groq_fast"])
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-placeholder")
    client = MultiProviderClient()

    async def execute():
        entered = asyncio.Event()

        async def handler(_request):
            entered.set()
            await asyncio.Event().wait()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            client.http_client = transport
            task = asyncio.create_task(client.execute_chat_completion(ChatCompletionRequest(
                messages=[{"role": "user", "content": "Hi"}],
            )))
            try:
                await asyncio.wait_for(entered.wait(), 2)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                client.http_client = None
        assert state(breaker)["status"] == ProviderStatus.UNHEALTHY
        assert not state(breaker)["probe_in_flight"]
        clock[0] = 60
        newer = breaker.acquire("groq_fast")
        assert newer is not None
        assert breaker.record_success("groq_fast", admission=newer)

    asyncio.run(execute())


def test_exception_immediately_after_admission_still_releases_probe(controlled, monkeypatch):
    breaker, clock = controlled
    open_circuit(breaker)
    clock[0] = 30
    monkeypatch.setattr("app.services.provider_client.circuit_breaker", breaker)
    monkeypatch.setattr(settings, "PROVIDER_ORDER", ["groq_fast"])
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-placeholder")
    calls = []

    def monotonic():
        calls.append(True)
        if len(calls) == 4:  # Outer start, deadline, remaining budget, then post-admission timer.
            raise RuntimeError("simulated timer failure")
        return 0.0

    monkeypatch.setattr("app.services.provider_client.time", SimpleNamespace(monotonic=monotonic))
    with pytest.raises(RuntimeError, match="simulated timer failure"):
        asyncio.run(MultiProviderClient().execute_chat_completion(ChatCompletionRequest(
            messages=[{"role": "user", "content": "Hi"}],
        )))
    assert state(breaker)["status"] == ProviderStatus.UNHEALTHY
    assert not state(breaker)["probe_in_flight"]
    clock[0] = 60
    assert breaker.acquire("groq_fast") is not None
