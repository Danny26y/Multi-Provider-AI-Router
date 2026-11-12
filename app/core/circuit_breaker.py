import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict

from app.core.metrics import record_circuit_state, record_transition


class ProviderStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, eq=False)
class CircuitAdmission:
    """Opaque, single-use permission belonging to one breaker/provider epoch."""

    provider: str
    generation: int


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
                "generation": 0,
                "admissions": set(),
            }
        return self._states[provider_name]

    def is_available(self, provider_name: str) -> bool:
        """Observe availability without claiming a recovery probe; use acquire to send."""
        with self._lock:
            state = self._get_provider_state(provider_name)
            record_circuit_state(provider_name, state["status"].value)
            return state["status"] == ProviderStatus.HEALTHY or (
                state["status"] == ProviderStatus.UNHEALTHY and time.monotonic() >= state["cooldown_until"]
            )

    def _transition(self, provider_name: str, state: dict, status: ProviderStatus) -> None:
        """Called only under _lock. Every transition fences all older callbacks."""
        if state["status"] != status:
            record_transition(provider_name, state["status"].value, status.value)
            state["generation"] += 1
            state["admissions"].clear()
            state["status"] = status
        state["probe_in_flight"] = status == ProviderStatus.DEGRADED
        record_circuit_state(provider_name, status.value)

    def acquire(self, provider_name: str) -> CircuitAdmission | None:
        """Atomically admit a healthy request or the sole half-open recovery probe."""
        with self._lock:
            state = self._get_provider_state(provider_name)
            if state["status"] == ProviderStatus.UNHEALTHY:
                if time.monotonic() < state["cooldown_until"]:
                    return None
                self._transition(provider_name, state, ProviderStatus.DEGRADED)
            elif state["status"] == ProviderStatus.DEGRADED:
                return None
            admission = CircuitAdmission(provider_name, state["generation"])
            state["admissions"].add(admission)
            record_circuit_state(provider_name, state["status"].value)
            return admission

    def _consume(self, provider_name: str, state: dict, admission: CircuitAdmission | None) -> bool:
        if (
            not isinstance(admission, CircuitAdmission) or admission.provider != provider_name
            or admission.generation != state["generation"] or admission not in state["admissions"]
        ):
            return False
        state["admissions"].remove(admission)
        return True

    def record_success(self, provider_name: str, *, admission: CircuitAdmission | None = None) -> bool:
        with self._lock:
            state = self._states.get(provider_name)
            if state is None or not self._consume(provider_name, state, admission):
                return False
            state["failures"] = 0
            self._transition(provider_name, state, ProviderStatus.HEALTHY)
            return True

    def record_failure(
        self,
        provider_name: str,
        force_open: bool = False,
        retry_after_seconds: float | None = None,
        *,
        admission: CircuitAdmission | None = None,
    ) -> bool:
        with self._lock:
            state = self._states.get(provider_name)
            if state is None or not self._consume(provider_name, state, admission):
                return False
            state["last_failure"] = time.monotonic()
            cooldown = max(self.recovery_time_seconds, retry_after_seconds or 0.0)
            state["cooldown_until"] = max(state["cooldown_until"], state["last_failure"] + cooldown)
            if state["status"] == ProviderStatus.DEGRADED or force_open:
                state["failures"] = self.failure_threshold
                self._transition(provider_name, state, ProviderStatus.UNHEALTHY)
                return True

            state["failures"] += 1
            if state["failures"] >= self.failure_threshold:
                self._transition(provider_name, state, ProviderStatus.UNHEALTHY)
            record_circuit_state(provider_name, state["status"].value)
            return True

    def record_neutral(self, provider_name: str, *, admission: CircuitAdmission | None = None) -> bool:
        """Release an inconclusive recovery probe without adding a health failure."""
        with self._lock:
            state = self._states.get(provider_name)
            if state is None or not self._consume(provider_name, state, admission):
                return False
            if state["status"] == ProviderStatus.DEGRADED:
                state["cooldown_until"] = max(state["cooldown_until"], time.monotonic() + self.recovery_time_seconds)
                self._transition(provider_name, state, ProviderStatus.UNHEALTHY)
            return True

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
