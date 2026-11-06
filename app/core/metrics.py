from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest


HTTP_REQUESTS = Counter(
    "ai_router_http_requests_total",
    "HTTP requests handled by the router.",
    ["method", "path", "status_code"],
)
HTTP_LATENCY = Histogram(
    "ai_router_http_request_duration_seconds",
    "HTTP request duration.",
    ["method", "path"],
)
PROVIDER_FAILURES = Counter(
    "ai_router_provider_failures_total",
    "Provider failures by safe category.",
    ["provider", "category", "retryable"],
)
PROVIDER_LATENCY = Histogram(
    "ai_router_provider_request_duration_seconds",
    "Provider request duration.",
    ["provider"],
)
CIRCUIT_STATE = Gauge(
    "ai_router_circuit_state",
    "Circuit state encoded as healthy=0, degraded=1, unhealthy=2.",
    ["provider"],
)


def record_circuit_state(provider: str, status: str) -> None:
    CIRCUIT_STATE.labels(provider=provider).set({"healthy": 0, "degraded": 1, "unhealthy": 2}[status])


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
