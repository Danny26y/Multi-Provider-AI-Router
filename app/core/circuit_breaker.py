import threading
import time
from enum import Enum
from typing import Dict

from app.core.metrics import record_circuit_state, record_transition


class ProviderStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_time_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_time_seconds = recovery_time_seconds
        self._states: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def _get_provider_state(self, provider_name: str) -> dict:
        if provider_name not in self._states:
            self._states[provider_name] = {
                "failures": 0,
                "last_failure": 0.0,
                "cooldown_until": 0.0,
                "status": ProviderStatus.HEALTHY,
                "probe_in_flight": False,
            }
        return self._states[provider_name]

    def is_available(self, provider_name: str) -> bool:
        with self._lock:
            state = self._get_provider_state(provider_name)
            if state["status"] == ProviderStatus.HEALTHY:
                record_circuit_state(provider_name, state["status"].value)
                return True
            if state["status"] == ProviderStatus.DEGRADED:
                record_circuit_state(provider_name, state["status"].value)
                return False

            if time.monotonic() < state["cooldown_until"]:
                record_circuit_state(provider_name, state["status"].value)
                return False

            record_transition(provider_name, state["status"].value, ProviderStatus.DEGRADED.value)
            state["status"] = ProviderStatus.DEGRADED
            state["probe_in_flight"] = True
            record_circuit_state(provider_name, state["status"].value)
            return True

    def record_success(self, provider_name: str) -> None:
        with self._lock:
            state = self._get_provider_state(provider_name)
            # An older concurrent request cannot clear a circuit opened since it started.
            # Recovery must first be admitted by is_available as a half-open probe.
            if state["status"] == ProviderStatus.UNHEALTHY:
                return
            state["failures"] = 0
            record_transition(provider_name, state["status"].value, ProviderStatus.HEALTHY.value)
            state["status"] = ProviderStatus.HEALTHY
            state["probe_in_flight"] = False
            record_circuit_state(provider_name, state["status"].value)

    def record_failure(
        self,
        provider_name: str,
        force_open: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        with self._lock:
            state = self._get_provider_state(provider_name)
            state["last_failure"] = time.monotonic()
            cooldown = max(self.recovery_time_seconds, retry_after_seconds or 0.0)
            state["cooldown_until"] = max(state["cooldown_until"], state["last_failure"] + cooldown)
            state["probe_in_flight"] = False

            if state["status"] == ProviderStatus.DEGRADED or force_open:
                state["failures"] = self.failure_threshold
                record_transition(provider_name, state["status"].value, ProviderStatus.UNHEALTHY.value)
                state["status"] = ProviderStatus.UNHEALTHY
                record_circuit_state(provider_name, state["status"].value)
                return

            state["failures"] += 1
            if state["failures"] >= self.failure_threshold:
                record_transition(provider_name, state["status"].value, ProviderStatus.UNHEALTHY.value)
                state["status"] = ProviderStatus.UNHEALTHY
            record_circuit_state(provider_name, state["status"].value)

    def record_neutral(self, provider_name: str) -> None:
        """Release an inconclusive recovery probe without adding a health failure."""
        with self._lock:
            state = self._get_provider_state(provider_name)
            if state["status"] == ProviderStatus.DEGRADED:
                state["probe_in_flight"] = False
                record_transition(provider_name, state["status"].value, ProviderStatus.UNHEALTHY.value)
                state["status"] = ProviderStatus.UNHEALTHY
                state["cooldown_until"] = time.monotonic() + self.recovery_time_seconds
                record_circuit_state(provider_name, state["status"].value)

    def snapshot(self) -> Dict[str, dict]:
        with self._lock:
            return {
                provider: {
                    "failures": state["failures"],
                    "status": state["status"],
                    "probe_in_flight": state["probe_in_flight"],
                }
                for provider, state in self._states.items()
            }


circuit_breaker = CircuitBreaker()
