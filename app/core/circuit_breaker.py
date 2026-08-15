import time
from enum import Enum
from typing import Dict, Optional

class ProviderStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"

class CircuitBreaker:
    def __init__(self, failure_threshold: int=3, recovery_time_seconds: float=30.0):
        self.failure_threshold = failure_threshold
        self.recovery_time_seconds = recovery_time_seconds

        self._states: Dict[str, dict] ={}

    def _get_provider_state(self, provider_name: str)-> dict:
        if provider_name not in self._states:
            self._states[provider_name] = {
                "failures":0,
                "last_failure":0.0,
                "status":ProviderStatus.HEALTHY
            }
        return self._states[provider_name]

    def is_available(self, provider_name: str)-> bool:
        state = self._get_provider_state(provider_name)

        if state["status"] == ProviderStatus.HEALTHY:
            return True

        time_since_failure = time.time() - state["last_failure"]
        if time_since_failure >= self.recovery_time_seconds:
            print(f"[CIRCUIT BREAKER]: Cooldown expired for {provider_name}. Testing recovery(DEGRADED state)...")
            state["status"] = ProviderStatus.DEGRADED
            return True
        print(f"[CIRCUIT BREAKER]: Provider '{provider_name}' is UNHEALTHY. Bypassing (Cooldown remaining: {int(self.recovery_time_seconds)-time_since_failure}s)....")
        return False
    def record_success(self, provider_name: str):
        state = self._get_provider_state(provider_name)
        if state["failures"] > 0 or state["status"] != ProviderStatus.HEALTHY:
            print(f"[CIRCUIT BREAKER]: Provider '{provider_name}' fully recovered to HEALTHY status.")
        state["failures"] = 0
        state["status"] = ProviderStatus.HEALTHY

    def record_failure(self, provider_name: str, error_code: int = 500):
        state = self._get_provider_state(provider_name)
        state["failures"] += 1
        state["last_failure"] = time.time()
        print(f"[CIRCUIT BREAKER]: Error {error_code} on {provider_name}. Consecutive failures: {state['failures']}/self.failure_threshold")
        if state["failures"] >= self.failure_threshold or error_code == 429:
            state["status"] = ProviderStatus.UNHEALTHY
            print(f"[CIRCUIT BREAKER]: Circuit OPENED for '{provider_name}. Marking UNHEALTHY.")


circuit_breaker = CircuitBreaker()
