# Multi-Provider AI Router

## 1. Overview and explicit scope

This FastAPI gateway tries configured AI providers in order, tracks failures in an in-memory circuit breaker, and returns a deterministic local mock response when no eligible remote provider succeeds. It exposes `POST /v1/chat/completions` and `GET /health`.

The project demonstrates routing, failure handling, diagnostics, tests, Docker packaging, and CI. It is not presented as a production-ready AI platform.

## 2. Architecture and routing sequence

```text
Client -> FastAPI -> eligible providers in order -> first valid response
                                              \-> mock fallback
```

The configured order is:

1. `groq_fast`
2. `cerebras_primary`
3. `groq_reasoning`
4. `mock_fallback`

Remote providers without credentials are skipped. For an eligible remote provider, the router sends one request and either returns its valid response or records the failure before moving to the next provider. The mock fallback does not call an LLM.

## 3. Circuit-breaker behavior

Circuit-breaker state is held in memory per provider and resets when the process restarts.

- Three ordinary failures open a provider's circuit.
- An HTTP `429` opens its circuit immediately.
- An open circuit is bypassed for the default 30-second cooldown.
- After cooldown, the next eligibility check permits a recovery probe in a degraded state.
- A successful probe resets the provider to healthy; a failed probe reopens its circuit.

There is no automatic request retry loop. Each provider is attempted at most once during a routing pass when it is eligible.

## 4. Local setup and configuration

Python 3.10 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Configure only the providers you intend to use:

```ini
GROQ_API_KEY=your_groq_api_key
CEREBRAS_API_KEY=your_cerebras_api_key
REQUEST_TIMEOUT_SECONDS=10
```

Keep `.env` local. It is excluded by `.gitignore` and `.dockerignore`.

## 5. Run locally and API example/response

Start the API:

```powershell
python main.py
```

The server listens at `http://localhost:8000`; interactive OpenAPI documentation is at `http://localhost:8000/docs`.

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Explain the circuit breaker pattern."}]}'
```

With no remote credentials, the request returns the mock fallback. The values below are representative; identifiers, timestamps, and latency are generated at runtime.

```json
{
  "id": "chatcmpl-mock-1700000000",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "mock-local-fallback",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "[SYSTEM FALLBACK]: All eligible remote providers were unavailable. This response was served by the local mock fallback."
      },
      "finish_reason": "stop"
    }
  ],
  "provider_used": "mock_fallback",
  "fallback_used": true,
  "routing_diagnostics": [
    {"provider": "groq_fast", "outcome": "skipped", "category": "missing_credentials"},
    {"provider": "cerebras_primary", "outcome": "skipped", "category": "missing_credentials"},
    {"provider": "groq_reasoning", "outcome": "skipped", "category": "missing_credentials"},
    {"provider": "mock_fallback", "outcome": "success", "category": "mock_fallback"}
  ],
  "latency_seconds": 0.0,
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 25,
    "total_tokens": 35,
    "estimated_cost_usd": 0.0
  }
}
```

`fallback_used` distinguishes a mock response from a real-provider response. `routing_diagnostics` reports non-sensitive outcomes such as `missing_credentials`, `circuit_open`, `http_error`, `timeout`, and `invalid_response`. Client-facing provider failures return a generic HTTP 503 response.

`model` is accepted for OpenAI-style request compatibility, but the router uses each provider's configured default model. `stream: true` is rejected with HTTP 400 because streaming is not implemented.

## 6. Docker build, runtime configuration, health check, and stop

Build from the repository root:

```powershell
docker build -t multi-provider-ai-router .
```

Run without credentials to use the mock fallback:

```powershell
docker run --rm --name multi-provider-ai-router -p 8000:8000 multi-provider-ai-router
```

Pass secrets only at runtime. The following command reads local environment values without copying `.env` into the image:

```powershell
docker run --rm --name multi-provider-ai-router -p 8000:8000 --env-file .env multi-provider-ai-router
```

Verify the running container:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Stop a foreground container with `Ctrl+C`; for a detached container, run:

```powershell
docker stop multi-provider-ai-router
```

The image uses `python:3.12-slim`, runs Uvicorn on `0.0.0.0:8000`, and runs as an unprivileged `appuser`. `-p 8000:8000` maps the container port to the host.

## 7. Tests and CI

Run the deterministic local checks without provider credentials or paid API calls:

```powershell
python -m compileall app config main.py
python -c "import main"
python -m pytest -v
```

The test suite covers provider ordering, missing credentials, remote-provider failures, mock fallback behavior, safe API errors, and circuit-breaker transitions. `test_failover.py` is a manual local API smoke/load script for a separately running service; it is not a deterministic test suite.

GitHub Actions CI has passed. On pushes and pull requests targeting `main`, it installs `requirements.txt` on Ubuntu with Python 3.12, then runs the same compile, import, and pytest checks.

## 8. Limitations and non-claims

- Live Groq and Cerebras connectivity has not been verified by this project.
- Throughput, latency, and availability have not been benchmarked.
- The application has no authentication, TLS termination, or persistent state.
- Circuit-breaker state is process-local and resets on restart.
- Streaming and per-request model selection are not implemented.
- The mock fallback is a fixed local response, not generated model output.
