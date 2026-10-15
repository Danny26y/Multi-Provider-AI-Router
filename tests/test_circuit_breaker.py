from app.core.circuit_breaker import CircuitBreaker, ProviderStatus


def test_opens_after_failure_threshold_and_rejects_until_cooldown(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.core.circuit_breaker.time.time", lambda: clock[0])
    breaker = CircuitBreaker(failure_threshold=2, recovery_time_seconds=30)

    assert breaker.is_available("provider")
    breaker.record_failure("provider", 500)
    assert breaker.is_available("provider")
    breaker.record_failure("provider", 500)

    assert breaker._states["provider"]["status"] == ProviderStatus.UNHEALTHY
    assert not breaker.is_available("provider")

    clock[0] += 30
    assert breaker.is_available("provider")
    assert breaker._states["provider"]["status"] == ProviderStatus.DEGRADED


def test_successful_recovery_resets_breaker(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.core.circuit_breaker.time.time", lambda: clock[0])
    breaker = CircuitBreaker(failure_threshold=1, recovery_time_seconds=10)

    breaker.record_failure("provider", 500)
    clock[0] += 10
    assert breaker.is_available("provider")
    breaker.record_success("provider")

    state = breaker._states["provider"]
    assert state["status"] == ProviderStatus.HEALTHY
    assert state["failures"] == 0
    assert breaker.is_available("provider")


def test_failed_recovery_probe_reopens_and_can_retry_later(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.core.circuit_breaker.time.time", lambda: clock[0])
    breaker = CircuitBreaker(failure_threshold=2, recovery_time_seconds=10)

    breaker.record_failure("provider", 500)
    breaker.record_failure("provider", 500)
    clock[0] += 10
    assert breaker.is_available("provider")

    breaker.record_failure("provider", 500)
    assert breaker._states["provider"]["status"] == ProviderStatus.UNHEALTHY
    assert not breaker.is_available("provider")

    clock[0] += 10
    assert breaker.is_available("provider")
