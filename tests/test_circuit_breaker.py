from concurrent.futures import ThreadPoolExecutor

from app.core.circuit_breaker import CircuitBreaker, ProviderStatus


def test_opens_after_failure_threshold_and_allows_one_recovery_probe(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.core.circuit_breaker.time.monotonic", lambda: clock[0])
    breaker = CircuitBreaker(failure_threshold=3, recovery_time_seconds=30)

    breaker.record_failure("provider")
    breaker.record_failure("provider")
    assert breaker.is_available("provider")
    breaker.record_failure("provider")
    assert not breaker.is_available("provider")

    clock[0] += 30
    assert breaker.is_available("provider")
    assert breaker._states["provider"]["status"] == ProviderStatus.DEGRADED
    assert not breaker.is_available("provider")


def test_successful_recovery_resets_breaker(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.core.circuit_breaker.time.monotonic", lambda: clock[0])
    breaker = CircuitBreaker(failure_threshold=1, recovery_time_seconds=10)

    breaker.record_failure("provider")
    clock[0] += 10
    assert breaker.is_available("provider")
    breaker.record_success("provider")

    state = breaker.snapshot()["provider"]
    assert state["status"] == ProviderStatus.HEALTHY
    assert state["failures"] == 0
    assert breaker.is_available("provider")


def test_failed_recovery_probe_and_rate_limit_open_circuit_immediately(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.core.circuit_breaker.time.monotonic", lambda: clock[0])
    breaker = CircuitBreaker(failure_threshold=3, recovery_time_seconds=10)

    breaker.record_failure("rate_limited", force_open=True)
    assert not breaker.is_available("rate_limited")

    breaker.record_failure("provider")
    breaker.record_failure("provider")
    breaker.record_failure("provider")
    clock[0] += 10
    assert breaker.is_available("provider")
    breaker.record_failure("provider")
    assert not breaker.is_available("provider")


def test_concurrent_half_open_allows_only_one_recovery_probe(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.core.circuit_breaker.time.monotonic", lambda: clock[0])
    breaker = CircuitBreaker(failure_threshold=1, recovery_time_seconds=10)
    breaker.record_failure("provider")
    clock[0] += 10

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: breaker.is_available("provider"), range(8)))

    assert results.count(True) == 1
