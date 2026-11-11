"""Server-side dashboard networking and presentation mapping; no provider SDKs."""

import math
import re
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from prometheus_client.parser import text_string_to_metric_families

FAILURE_CATEGORIES = {
    "timeout", "connection_error", "rate_limited", "server_error", "invalid_response",
    "unusable_output", "model_mismatch", "auth_error", "provider_configuration_error",
    "unsupported_model", "invalid_request", "missing_credentials", "model_not_supported",
    "fallback_disabled", "deadline_exceeded", "circuit_open", "mock_fallback",
}
METRIC_NAMES = {
    "ai_router_http_requests_total", "ai_router_http_request_duration_seconds_sum",
    "ai_router_http_request_duration_seconds_count", "ai_router_provider_failures_total",
    "ai_router_provider_request_duration_seconds_sum", "ai_router_provider_request_duration_seconds_count",
    "ai_router_circuit_state",
    "ai_router_completions_total", "ai_router_provider_selections_total", "ai_router_fallback_selections_total",
    "ai_router_tokens_total", "ai_router_routing_duration_seconds_sum", "ai_router_routing_duration_seconds_count",
    "ai_router_circuit_transitions_total", "ai_router_http_inflight", "ai_router_chat_inflight",
    "ai_router_chat_queued", "ai_router_concurrency_limit", "ai_router_limit_rejections_total",
}
HEALTH_ENDPOINTS = ("/health", "/health/live", "/health/ready")
PROVIDER_HOSTS = {"integrate.api.nvidia.com", "api.groq.com", "api.cerebras.ai"}


class DashboardConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class DashboardConfig:
    base_url: str
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 30.0
    metrics_api_key: str = field(default="", repr=False)

    def __post_init__(self):
        try:
            url = urlsplit(self.base_url)
            valid = (
                url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password
                and not url.query and not url.fragment and url.hostname not in PROVIDER_HOSTS
                and (url.scheme == "https" or url.hostname in {"localhost", "127.0.0.1", "::1"})
                and all(not key or key not in self.base_url for key in (self.api_key, self.metrics_api_key))
                and math.isfinite(self.timeout_seconds) and 0 < self.timeout_seconds <= 120
                and all("\r" not in key and "\n" not in key for key in (self.api_key, self.metrics_api_key))
            )
            _ = url.port
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise DashboardConfigurationError(
                "Configure a router URL using HTTPS (HTTP is allowed on loopback), without credentials, "
                "query parameters, or fragments, and a request timeout between 0 and 120 seconds."
            )

    @classmethod
    def from_sources(cls, environ: Mapping, secrets: Mapping | None = None):
        secrets = secrets or {}

        def value(name, default=""):
            return environ.get(name, secrets.get(name, default))

        try:
            return cls(
                base_url=str(value("DASHBOARD_ROUTER_URL")).rstrip("/"),
                api_key=str(value("DASHBOARD_API_KEY", value("ROUTER_API_KEY"))),
                timeout_seconds=float(value("DASHBOARD_REQUEST_TIMEOUT_SECONDS", "30")),
                metrics_api_key=str(value("DASHBOARD_METRICS_API_KEY", value("METRICS_API_KEY"))),
            )
        except (TypeError, ValueError):
            raise DashboardConfigurationError(
                "Set DASHBOARD_ROUTER_URL and valid dashboard settings in the server environment or Streamlit secrets."
            ) from None


def number(value):
    try:
        return value if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def count(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def redact(value, secret=""):
    for key in (secret,) if isinstance(secret, str) else secret:
        if key:
            value = value.replace(key, "[redacted]")
    return value


def label(value, secret=""):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value):
        return None
    return redact(value, secret)


def diagnostics_view(entries, secret="") -> list[dict]:
    if not isinstance(entries, list):
        return []
    result = []
    for entry in entries[:20]:
        if not isinstance(entry, dict):
            continue
        outcome = entry.get("outcome")
        category = entry.get("category")
        result.append({
            "provider": label(entry.get("provider"), secret),
            "outcome": outcome if isinstance(outcome, str) and outcome in {"success", "failed", "skipped"} else None,
            "category": category if isinstance(category, str) and category in FAILURE_CATEGORIES else None,
            **{key: entry.get(key) if type(entry.get(key)) is bool else None for key in (
                "attempted", "selected", "fallback_used", "circuit_breaker_affected",
            )},
        })
    return result


def completion_view(data, secret="") -> dict:
    if not isinstance(data, dict):
        return {"outcome": "failed", "message": "Router returned an invalid completion."}
    choices = data.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip() or message.get("role") != "assistant":
        return {"outcome": "failed", "message": "Router returned an unusable completion."}
    finish = choice.get("finish_reason")
    if finish not in (None, "stop", "length"):
        return {"outcome": "failed", "message": "Router did not return a usable text completion."}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {
        "outcome": "truncated" if finish == "length" else "completed",
        "content": redact(content, secret),
        "provider": label(data.get("provider_used"), secret),
        "model": label(data.get("model"), secret),
        "finish_reason": finish,
        "provider_latency_seconds": number(data.get("latency_seconds")),
        "fallback_used": data.get("fallback_used") if type(data.get("fallback_used")) is bool else None,
        "usage": {
            **{name: count(usage.get(name)) for name in ("prompt_tokens", "completion_tokens", "total_tokens")},
            "estimated_cost_usd": number(usage.get("estimated_cost_usd")),
            **{key: usage.get(key) if type(usage.get(key)) is bool else None for key in (
                "token_usage_is_estimated", "cost_is_estimated",
            )},
        },
        "diagnostics": diagnostics_view(data.get("routing_diagnostics"), secret),
    }


def failure_view(status: int, data=None, secret="") -> dict:
    messages = {
        401: "Router authentication failed. Check the server-side dashboard API key.",
        403: "Router access was denied. Check the dashboard's authorization.",
        400: "Router rejected the request. Check the prompt and parameters.",
        422: "Router rejected the prompt, model, or token limit. Check its configured allowlist and limits.",
        429: "Router rate limit reached. Wait before sending another prompt.",
        503: "Router could not serve the request. Check readiness and routing diagnostics.",
    }
    error = data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), dict) else {}
    return {
        "outcome": "failed",
        "message": messages.get(status, "Router request failed. Check connection and service status."),
        "http_status": status,
        "error_code": error.get("code") if error.get("code") in ("deadline_exceeded", "no_provider_available") else None,
        "diagnostics": diagnostics_view(error.get("routing_diagnostics"), secret),
    }


def session_summary(history: list[dict]) -> dict:
    elapsed = [row["round_trip_seconds"] for row in history if number(row.get("round_trip_seconds")) is not None]
    observed = [row for row in history if row.get("outcome") in {"completed", "truncated"}]
    return {
        "requests": len(history),
        "completed": sum(row.get("outcome") == "completed" for row in history),
        "truncated": sum(row.get("outcome") == "truncated" for row in history),
        "failed": sum(row.get("outcome") == "failed" for row in history),
        "mean_round_trip_seconds": sum(elapsed) / len(elapsed) if elapsed else None,
        "reported_fallbacks": sum(row.get("fallback_used") is True for row in observed),
        "fallback_flags_reported": sum(type(row.get("fallback_used")) is bool for row in observed),
        "provider_selections": dict(Counter(row["provider"] for row in observed if row.get("provider"))),
    }


def metrics_view(text: str, secret="") -> list[dict]:
    rows = []
    try:
        for family in text_string_to_metric_families(text):
            for sample in family.samples:
                if sample.name not in METRIC_NAMES or number(sample.value) is None:
                    continue
                safe_labels = {key: label(value, secret) for key, value in sample.labels.items() if key in {
                    "provider", "method", "path", "status_code", "category", "retryable",
                    "outcome", "kind", "source", "from_state", "to_state", "limit",
                }}
                rows.append({"metric": sample.name, **safe_labels, "value": sample.value})
                if len(rows) >= 500:
                    return rows
    except (ValueError, TypeError):
        return []
    return rows


class DashboardClient:
    def __init__(self, config: DashboardConfig, transport=None):
        self.config = config
        self.transport = transport

    def _request(self, path, payload=None):
        authenticated = path == "/v1/chat/completions"
        if authenticated and not self.config.api_key:
            return {"outcome": "failed", "message": "Configure a server-side dashboard API key before sending prompts."}
        started = time.monotonic()
        try:
            with httpx.Client(
                transport=self.transport, follow_redirects=False, trust_env=False,
                timeout=httpx.Timeout(self.config.timeout_seconds if authenticated else min(5, self.config.timeout_seconds), connect=min(5, self.config.timeout_seconds)),
            ) as client:
                response = client.request(
                    "POST" if authenticated else "GET", self.config.base_url + path,
                    headers={"X-API-Key": self.config.api_key} if authenticated else (
                        {"X-Metrics-Key": self.config.metrics_api_key} if path == "/metrics" and self.config.metrics_api_key else {}
                    ),
                    **({"json": payload} if authenticated else {}),
                )
            try:
                data = response.json() if path != "/metrics" else None
            except (ValueError, RecursionError):
                data = None
            if response.status_code != 200:
                result = failure_view(response.status_code, data, (self.config.api_key, self.config.metrics_api_key))
                if path == "/metrics" and response.status_code in {401, 403}:
                    result["message"] = "Metrics access denied. Check the server-side dashboard metrics key."
            elif authenticated:
                result = completion_view(data, (self.config.api_key, self.config.metrics_api_key))
            elif path == "/metrics":
                result = {"outcome": "completed", "metrics": metrics_view(response.text, (self.config.api_key, self.config.metrics_api_key))}
            else:
                status = data.get("status") if isinstance(data, dict) else None
                expected = "ready" if path == "/health/ready" else "online"
                result = {"outcome": "completed", "status": status if status == expected else "unknown"}
        except httpx.TimeoutException:
            result = {"outcome": "failed", "message": "Router request timed out. Check service status before retrying."}
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            result = {"outcome": "failed", "message": "Could not connect to the router. Check its URL, TLS configuration, and availability."}
        result["round_trip_seconds"] = round(time.monotonic() - started, 3)
        return result

    def prompt(self, text, *, model="", temperature=0.7, max_tokens=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 20_000:
            return {"outcome": "failed", "message": "Enter a nonempty prompt of at most 20,000 characters."}
        if type(temperature) not in {int, float} or not 0 <= temperature <= 2:
            return {"outcome": "failed", "message": "Temperature must be between 0 and 2."}
        if max_tokens is not None and (type(max_tokens) is not int or max_tokens < 1):
            return {"outcome": "failed", "message": "The output token limit must be a positive integer."}
        payload = {"messages": [{"role": "user", "content": text}], "temperature": temperature, "stream": False}
        if model:
            payload["model"] = model
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return self._request("/v1/chat/completions", payload)

    def health(self):
        return {path: self._request(path) for path in HEALTH_ENDPOINTS}

    def metrics(self):
        return self._request("/metrics")
