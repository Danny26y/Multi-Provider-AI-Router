from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

PROVIDERS = {"groq_fast", "nvidia", "groq_reasoning", "cerebras_primary", "mock_fallback", "none"}
PATHS = {"/health", "/health/live", "/health/ready", "/metrics", "/v1/chat/completions", "/docs", "/openapi.json", "/redoc"}
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}


def provider_label(provider: str) -> str:
    return provider if provider in PROVIDERS else "other"


def request_labels(method: str, path: str) -> tuple[str, str]:
    return (method if method in METHODS else "OTHER", path if path in PATHS else "unmatched")


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

COMPLETIONS = Counter("ai_router_completions_total", "One terminal routing outcome per completion request.", ["provider", "outcome"])
SELECTIONS = Counter("ai_router_provider_selections_total", "Validated responses selected, once per request.", ["provider"])
FALLBACKS = Counter("ai_router_fallback_selections_total", "Selected fallback responses, once per request.", ["provider"])
TOKENS = Counter("ai_router_tokens_total", "Selected response tokens, separated by estimation source.", ["provider", "kind", "source"])
ROUTING_LATENCY = Histogram("ai_router_routing_duration_seconds", "End-to-end routing duration including failover.", ["outcome"])
CIRCUIT_TRANSITIONS = Counter("ai_router_circuit_transitions_total", "Actual circuit status changes.", ["provider", "from_state", "to_state"])
HTTP_INFLIGHT = Gauge("ai_router_http_inflight", "HTTP requests currently handled.")
CHAT_INFLIGHT = Gauge("ai_router_chat_inflight", "Chat requests holding an inference concurrency slot.")
CHAT_QUEUED = Gauge("ai_router_chat_queued", "Chat requests waiting for an inference slot.")
CONCURRENCY_LIMIT = Gauge("ai_router_concurrency_limit", "Configured per-process inference slot limit.")
LIMIT_REJECTIONS = Counter("ai_router_limit_rejections_total", "Rejected requests by bounded limit type.", ["limit"])


def record_completion(response) -> None:
    provider = provider_label(response.provider_used)
    outcome = "mock" if response.provider_used == "mock_fallback" else (
        "truncated" if any(choice.finish_reason == "length" for choice in response.choices) else "success"
    )
    COMPLETIONS.labels(provider=provider, outcome=outcome).inc()
    SELECTIONS.labels(provider=provider).inc()
    if response.fallback_used:
        FALLBACKS.labels(provider=provider).inc()
    if outcome != "mock":
        source = "estimated" if response.usage.token_usage_is_estimated else "reported"
        for kind in ("prompt", "completion", "total"):
            TOKENS.labels(provider=provider, kind=kind, source=source).inc(getattr(response.usage, f"{kind}_tokens"))


def record_circuit_state(provider: str, status: str) -> None:
    CIRCUIT_STATE.labels(provider=provider_label(provider)).set({"healthy": 0, "degraded": 1, "unhealthy": 2}[status])


def record_transition(provider: str, old: str, new: str) -> None:
    if old != new:
        CIRCUIT_TRANSITIONS.labels(provider=provider_label(provider), from_state=old, to_state=new).inc()


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
