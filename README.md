# Multi-Provider AI Router

## Overview

This FastAPI gateway routes chat-completion requests across configured AI providers, applies circuit-breaker controls, and can use a deterministic mock fallback for local development and tests. It exposes `POST /v1/chat/completions`, public `GET /health/live`, and diagnostic `GET /health` endpoints.

The project demonstrates secure request handling, provider routing, deterministic tests, Docker packaging, and CI. It is not presented as production-ready.

## Routing and circuit breakers

Providers are evaluated in this order: `groq_fast`, `cerebras_primary`, `groq_reasoning`, then the optional `mock_fallback`.

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

Supported message roles are `system`, `user`, and `assistant`. Requests require at least one nonempty message, constrain message and total content size, accept temperatures from `0` to `2`, and accept `max_tokens` from `1` to `4096`. Extra fields are rejected. Explicit model values must match a configured provider model; the defaults are `llama-3.1-8b-instant`, `zai-glm-4.7`, and `llama-3.3-70b-versatile`. Omit `model` to preserve provider-order failover. Streaming is not supported.

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

## Reliability controls

The application owns one `httpx.AsyncClient` for its lifespan instead of opening a new connection pool per upstream request. It applies configurable connect, read, write, and pool timeouts plus one deadline across the complete provider-routing pass.

Client limits are in-process and use the connecting client IP: `CLIENT_RATE_LIMIT_PER_MINUTE` returns HTTP 429 with `Retry-After`, and `MAX_CONCURRENT_REQUESTS` caps concurrent chat handling. These limits and circuit-breaker state are per process. They are appropriate for a single worker; deploy shared Redis-backed state only when scaling to multiple workers or replicas requires globally coordinated limits and breakers.

Provider usage is returned as actual token usage when the upstream response contains complete usage fields. Otherwise the router marks the usage as estimated. `estimated_cost_usd` uses the configured `MODEL_PRICES_USD_PER_MILLION_TOKENS` mapping and is always an estimate.

## Tests and CI

Run deterministic checks without provider credentials or paid API calls:

```powershell
python -m compileall app config main.py
python -c "import main"
python -m pytest -v
```

`test_failover.py` is a manual local API smoke/load script for a separately running service, not a deterministic test suite.

GitHub Actions CI runs the compile, import, and pytest checks on Ubuntu with Python 3.12 for pushes and pull requests to `main`.

## Limitations

- Live Groq and Cerebras connectivity is not verified by this repository.
- Throughput, latency, and availability are not benchmarked.
- The service has no TLS termination, persistent state, or streaming support.
- Rate limiting and circuit-breaker state are not shared between workers or replicas.
- The mock fallback is fixed text, not generated model output.
- Model selection is limited to the configured provider defaults; an explicit model narrows routing to compatible providers.
