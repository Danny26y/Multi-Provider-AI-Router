# Multi-Provider AI Router

## Overview

This FastAPI gateway routes chat-completion requests across configured AI providers, applies circuit-breaker controls, and can use a deterministic mock fallback for local development and tests. It exposes `POST /v1/chat/completions`, public `GET /health/live`, and diagnostic `GET /health` endpoints.

The project demonstrates secure request handling, provider routing, deterministic tests, Docker packaging, and CI. It is not presented as production-ready.

## Routing and circuit breakers

Providers are evaluated in this order: `groq_fast`, `nvidia`, `groq_reasoning`, then the optional `mock_fallback`. Cerebras remains supported and is disabled by default; enable `CEREBRAS_ENABLED` and include `cerebras_primary` in `PROVIDER_ORDER` to use it.

- Providers without credentials, with open circuits, or incompatible with an explicitly requested model are skipped.
- A request is sent at most once to an eligible provider; there is no retry loop.
- Timeouts, connection errors, HTTP 429, 5xx responses, and malformed upstream responses are retryable failures and permit failover.
- Upstream authentication and invalid-request responses do not open a circuit.
- Three retryable failures open a circuit; HTTP 429 opens it immediately.
- After the 30-second default cooldown, exactly one recovery probe is allowed. A successful probe restores the provider; a failed probe reopens the circuit.
- The `Retry-After` header from an upstream 429 extends that provider's cooldown, up to the configured maximum.

Circuit-breaker state is process-local and resets when the application restarts.

## Setup and configuration

Python 3.10 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set a long, random router key before serving chat requests:

```ini
ROUTER_API_KEY=generate_a_long_random_value
APP_ENV=development
ENABLE_MOCK_FALLBACK=false
GROQ_API_KEY=your_groq_api_key
CEREBRAS_API_KEY=your_cerebras_api_key
```

Provider order, default models, timeout phases, the total routing deadline, local client limits, fallback policy, and per-model pricing are configurable through environment variables. See `.env.example` for the complete set. Collection values such as `PROVIDER_ORDER` and `MODEL_PRICES_USD_PER_MILLION_TOKENS` use JSON syntax.

`X-API-Key` authentication is required for `POST /v1/chat/completions`. If `ROUTER_API_KEY` is absent, the endpoint fails closed with HTTP 503. Keep `.env` local; it is excluded by `.gitignore` and `.dockerignore`.

The mock fallback is disabled by default. It can be enabled only when `APP_ENV` is `development` or `testing` and `ENABLE_MOCK_FALLBACK=true`. Production requests receive HTTP 503 when no real provider returns a valid response.

## Run and use the API

```powershell
python main.py
```

The server listens at `http://localhost:8000`; OpenAPI documentation is at `http://localhost:8000/docs`.

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_router_api_key" \
  -d '{"messages":[{"role":"user","content":"Explain the circuit breaker pattern."}]}'
```

Supported message roles are `system`, `user`, and `assistant`. Requests require at least one nonempty message, constrain message and total content size, accept temperatures from `0` to `2`, and accept `max_tokens` from `1` to `MAX_OUTPUT_TOKENS` (default `16384`). Extra fields are rejected. Explicit model values must match a configured provider model or `NVIDIA_ALLOWED_MODELS`; the NVIDIA default is `nvidia/nemotron-3.5-lightning-30b-a3b`. Explicit models never fall back to a different model or the mock provider. Omit `model` to preserve provider-order failover. Streaming is not supported.

When development fallback is enabled and no remote provider is eligible, the response contains `provider_used: "mock_fallback"`, `fallback_used: true`, and non-sensitive `routing_diagnostics`. Real-provider responses set `fallback_used` to `false`. Provider response bodies, API keys, and request content are not included in diagnostics or client-facing errors.

## Docker

Build the non-root Python 3.12 image from the repository root:

```powershell
docker build -t multi-provider-ai-router .
```

Run with a local `.env` file; the file is read at runtime and is not copied into the image:

```powershell
docker run --rm --name multi-provider-ai-router -p 8000:8000 --env-file .env multi-provider-ai-router
```

Verify liveness without credentials:

```powershell
Invoke-RestMethod http://localhost:8000/health/live
```

Stop a foreground container with `Ctrl+C`; for a detached container, use:

```powershell
docker stop multi-provider-ai-router
```

The image binds Uvicorn to `0.0.0.0:8000` and runs as unprivileged `appuser`; `-p 8000:8000` maps the container port to the host.

Docker also checks `GET /health/live` every 30 seconds after startup. Inspect the resulting health status with:

```powershell
docker inspect --format '{{.State.Health.Status}}' multi-provider-ai-router
```

## Reliability controls

The application owns one `httpx.AsyncClient` for its lifespan instead of opening a new connection pool per upstream request. It applies configurable connect, read, write, and pool timeouts plus one deadline across the complete provider-routing pass.

Client limits are in-process and use the connecting client IP: `CLIENT_RATE_LIMIT_PER_MINUTE` returns HTTP 429 with `Retry-After`, and `MAX_CONCURRENT_REQUESTS` caps concurrent chat handling. These limits and circuit-breaker state are per process. They are appropriate for a single worker; deploy shared Redis-backed state only when scaling to multiple workers or replicas requires globally coordinated limits and breakers.

Provider usage is returned as actual token usage when the upstream response contains complete usage fields. Otherwise the router marks the usage as estimated. `estimated_cost_usd` uses the configured `MODEL_PRICES_USD_PER_MILLION_TOKENS` mapping and is always an estimate.

## Observability

Application logs are JSON records with a request correlation ID. Send `X-Request-ID` to preserve an existing ID, or use the generated ID returned in the response header. Logs and diagnostics contain only safe metadata: provider name, failure category, HTTP status, and latency; they do not include API keys or prompt content.

Prometheus-compatible metrics are exposed at `GET /metrics`:

- `ai_router_http_requests_total` and `ai_router_http_request_duration_seconds`
- `ai_router_provider_failures_total` and `ai_router_provider_request_duration_seconds`
- `ai_router_circuit_state` (`healthy=0`, `degraded=1`, `unhealthy=2`)

`GET /health/live` reports whether the process is running. `GET /health/ready` returns 200 only when chat authentication is configured and at least one remote provider or enabled development fallback is available. `GET /health` remains a minimal compatibility status endpoint and does not expose circuit-breaker internals.

## Tests and CI

Install the development dependencies, then run deterministic checks without provider credentials or paid API calls:

```powershell
python -m pip install -r requirements-dev.txt
python -m compileall app config main.py
python -c "import main"
python -m pytest -v
```

`test_failover.py` is a manual local API smoke/load script for a separately running service, not a deterministic test suite.

GitHub Actions CI runs the compile, import, and pytest checks on Ubuntu with Python 3.12 for pushes and pull requests to `main`.

Install developer-only tooling with `python -m pip install -r requirements-dev.txt`. CI then runs Ruff linting and formatting checks, mypy, `pip-audit`, and coverage reporting in addition to the deterministic tests.

`requirements.txt` contains the runtime dependencies used by the container. `requirements-dev.txt` adds tests and CI tooling; `requirements-dashboard.txt` adds the optional Streamlit dashboard; and `requirements-benchmark.txt` adds Locust.

## Engineering dashboard

The optional Streamlit dashboard sends inference requests only through the secured
router. Configure `DASHBOARD_ROUTER_URL` and `DASHBOARD_API_KEY` on the dashboard
server, using environment variables or Streamlit secrets. If `DASHBOARD_API_KEY`
is omitted, it uses the server-side `ROUTER_API_KEY`. Environment variables take
precedence over secrets. The URL has no application default; `.env.example`
contains a loopback example for local demonstrations.

```powershell
python -m pip install -r requirements-dashboard.txt
python -m streamlit run dashboard.py
```

Use HTTPS for remote routers; HTTP is allowed only on loopback. The dashboard
does not display keys or full connection URLs and does not follow redirects.
`DASHBOARD_REQUEST_TIMEOUT_SECONDS` defaults to 30 (maximum 120); public health
and metrics probes use at most five seconds per HTTP phase. No request is retried
automatically. Keep the dashboard timeout longer than the router's total deadline
plus network overhead.

The prompt panel supports an optional explicit model, temperature, and an opt-in
token-budget override. Leaving model and token override unset preserves router
defaults and normal fallback. Truncated answers are visibly marked. Session
history retains at most 100 observations without prompts or answer text; only the
latest answer is retained separately in the server-side browser session.

Health checks report only supported status fields. They do not infer circuits
from `/health`. Server metrics are a separate, manually refreshed Prometheus
snapshot; charts describe observations in the current dashboard session. Missing
provider, usage, latency, or diagnostic values remain unreported.

Dashboard client tests run with the normal development dependencies. Install the
dashboard requirements to also run the optional Streamlit UI tests:

```powershell
python -m pytest tests/test_dashboard_client.py tests/test_dashboard_ui.py -v
```

Protect access to the dashboard with your deployment's authentication controls:
anyone able to use it can send requests using its server-held router credential.
Do not put secrets in URLs, prompts, committed files, or Streamlit widget defaults.
The existing public `/metrics` endpoint remains unchanged; restrict its network
exposure through your existing ingress controls. This checkpoint adds no public
status endpoints or new infrastructure.

For repeatable load testing against a local mock upstream, follow [benchmarks/README.md](benchmarks/README.md). The harness produces Locust CSV data for throughput, p50, p95, and error rate; no benchmark figures are claimed until a run is recorded.

## Production-oriented documentation

- [Architecture](docs/architecture.md)
- [Threat model](docs/threat-model.md)
- [API examples](docs/api.md)
- [Operational runbook](docs/operations.md)
- [Benchmark results](docs/benchmark-results.md)

Deploy behind an HTTPS-capable reverse proxy or load balancer, inject secrets through the deployment platform, and set explicit CPU, memory, replica, and concurrency limits there. This repository intentionally does not claim a staging or production deployment; the runbook defines the required staging smoke-test and rollback process.

## Limitations

- Live Groq, NVIDIA, and Cerebras connectivity is not verified by this repository.
- Throughput, latency, and availability are not benchmarked.
- The service has no TLS termination, persistent state, or streaming support.
- Rate limiting and circuit-breaker state are not shared between workers or replicas.
- The mock fallback is fixed text, not generated model output.
- Model selection is limited to the configured provider defaults; an explicit model narrows routing to compatible providers.
