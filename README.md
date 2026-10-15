# Multi-Provider AI Router

A FastAPI gateway that attempts configured AI providers in order, tracks provider failures with a circuit breaker, and returns a local mock response when no eligible remote provider succeeds.

## Request flow

```text
POST /v1/chat/completions
  -> validate request
  -> try eligible providers in configured order
  -> record success or failure in the circuit breaker
  -> return the first valid remote response, or mock fallback
```

The current order is `groq_fast`, `cerebras_primary`, `groq_reasoning`, then `mock_fallback`. A provider without credentials is skipped. A failed provider is retried only after the configured cooldown; its next request is a recovery probe.

## Setup

Python 3.10 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set only the provider credentials you intend to use in `.env`:

```ini
GROQ_API_KEY=your_groq_api_key
CEREBRAS_API_KEY=your_cerebras_api_key
```

Do not commit `.env`; it is excluded by `.gitignore`.

## Run

```powershell
python main.py
```

The API is available at `http://localhost:8000`, with interactive docs at `http://localhost:8000/docs`.

In another terminal, launch the optional monitoring dashboard:

```powershell
streamlit run dashboard.py
```

## API example

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Explain the circuit breaker pattern."}]}'
```

Responses retain `provider_used` and add:

- `fallback_used`: `true` only when the mock fallback generated the response.
- `routing_diagnostics`: non-sensitive provider outcomes such as `missing_credentials`, `http_error`, `timeout`, or `circuit_open`.

The API never returns raw provider exception messages or response bodies to clients.

## Current limitations

- A request's `model` field is accepted for OpenAI-style compatibility, but routing uses each provider's configured default model. Per-request model selection is not implemented.
- `stream: true` is explicitly rejected with HTTP 400; streaming responses are not implemented.
- The mock fallback is deterministic and does not call an LLM. A 200 response from it does not verify that any real provider works.
- Provider availability depends on valid credentials, provider account access, and network connectivity. This repository does not claim live-provider verification.

## Tests

Run deterministic tests without API keys or paid requests:

```powershell
pytest
python -m compileall app config main.py
```

`test_failover.py` is a manual smoke/load script for a separately running local API:

```powershell
python test_failover.py
```

It can exercise live configuration, but is not an automated or deterministic test suite.

## Health endpoint

`GET /health` returns `online` and in-memory circuit-breaker state. The state resets when the application process restarts.

## Portfolio next step

Add a small Docker run path and GitHub Actions workflow after reviewing the stabilized local test results. Neither is included yet so the packaging change remains focused.
