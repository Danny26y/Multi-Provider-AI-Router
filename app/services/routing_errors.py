from app.schemas.requests import ProviderDiagnostic


def routing_diagnostics(entries: list[dict]) -> list[ProviderDiagnostic]:
    result = []
    prior_failure = False
    for entry in entries:
        selected = entry["outcome"] == "success"
        attempted = entry["outcome"] in {"failed", "success"} and entry.get("category") != "mock_fallback"
        result.append(ProviderDiagnostic(
            **entry,
            attempted=attempted,
            selected=selected,
            fallback_used=(prior_failure and (attempted or selected)) or entry.get("category") == "mock_fallback",
            circuit_breaker_affected=entry.get("category") == "circuit_open",
        ))
        if entry["outcome"] == "failed" or entry.get("category") in {
            "circuit_open", "missing_credentials", "provider_configuration_error",
        }:
            prior_failure = True
    return result


class RoutingError(RuntimeError):
    def __init__(self, diagnostics: list[dict], deadline_exceeded: bool = False):
        super().__init__("No eligible AI provider produced a valid response.")
        self.code = "deadline_exceeded" if deadline_exceeded else "no_provider_available"
        self.diagnostics = routing_diagnostics(diagnostics)

    def public_error(self) -> dict:
        return {
            "code": self.code,
            "message": "No AI provider is currently available.",
            "fallback_used": any(item.fallback_used for item in self.diagnostics),
            "routing_diagnostics": [item.model_dump() for item in self.diagnostics],
        }
