# Multi-Provider AI Router

A secured, asynchronous FastAPI gateway for text chat completions across **Groq,
NVIDIA NIM, and optional Cerebras**. Clients use one API while the router handles
provider eligibility, response validation, failover, and a shared request deadline.

This engineering portfolio project addresses a practical reliability problem:
an HTTP 200 from an AI provider does not necessarily contain a usable answer,
and a fallback must preserve the caller's model constraints and latency budget.
The repository includes deterministic tests, a no-cost local demonstration,
an optional Streamlit dashboard, Prometheus instrumentation, and container/CI
checks. It is **not a production-deployed service**.

## How routing works

The default remote order is **Groq fast → NVIDIA NIM → Groq reasoning**.
Cerebras is supported but disabled by default. The optional local mock fallback
is also disabled and is allowed only in development/testing.

1. Check remaining deadline, credentials, model allowlist, and circuit admission.
2. Attempt each eligible route once, using a shared asynchronous HTTP pool.
3. Validate JSON, choices, assistant text, finish reason, returned model identity,
   and supplied usage before selecting a response.
4. Continue to the next eligible route after failure; return a sanitized HTTP 503
   if none succeeds. There is no per-provider retry loop.

An explicit `model` is never silently replaced. NVIDIA defaults to
`nvidia/nemotron-3.5-lightning-30b-a3b`; its allowlist and token budgets are
configurable. Reasoning-only output is unusable even if all generation tokens
were consumed. Visible text ending with `finish_reason: length` retains that
reason so clients can flag possible truncation.

Availability/reliability failures affect the circuit; authentication, model
configuration, and unusable output do not add health failures. Three health
failures open a circuit; HTTP 429 opens it immediately with a provider-scoped,
bounded `Retry-After` cooldown. After the default 30-second recovery interval,
one half-open probe is admitted. Inconclusive probes release their slot and wait
another interval. State is process-local. See [architecture](docs/architecture.md)
and [failure classification](docs/operations.md#failure-classification-and-circuit-health).

## Install on Python 3.12

From the repository root in Windows PowerShell:

```powershell
py -3.12 -m venv .venv/local
$Python = "$PWD/.venv/local/Scripts/python.exe"
& $Python -m pip install uv==0.13.4
$env:Path = "$PWD/.venv/local/Scripts;$env:Path"
& $Python scripts/lock_dependencies.py --platform windows
& $Python -m pip install --require-hashes -r .locks/ci.txt
Copy-Item .env.example .env
```

The CI lock includes development/test and optional dashboard dependencies.
For runtime only, install `.locks/runtime.txt` instead. On Linux use
`python3.12 -m venv .venv/local`, `.venv/local/bin/python`, add
`.venv/local/bin` to `PATH`, and generate with `--platform linux`.
The script writes the same two filenames for each platform: archive locks before
switching targets. Resolution requires network access; installation is hash-locked.
See [dependency policy](docs/deployment-readiness.md#dependency-and-ci-policy).

## Try it without provider access

```powershell
& $Python scripts/demo.py --scenario fallback --verify-only
& $Python scripts/demo.py --scenario fallback --dashboard
```

The second command leaves a loopback router, mock upstream, and dashboard running
until Ctrl+C. Open `http://127.0.0.1:8501`, check the connection, submit a prompt,
and inspect the selected provider and fallback diagnostics. Generated demo keys
stay in child-process environments. No real provider is called. Full scenarios
and expected results are in the [demonstration guide](docs/demo.md).

## Configure and run with real providers

Set values in the ignored local `.env` or a deployment secret store:

| Configuration | Purpose |
| --- | --- |
| `ROUTER_API_KEY` | Required shared secret for chat; absent key fails closed |
| `GROQ_API_KEY`, `NVIDIA_API_KEY` | Enable corresponding configured routes |
| `NVIDIA_ENABLED`, `NVIDIA_ALLOWED_MODELS` | NVIDIA eligibility/model policy |
| `CEREBRAS_ENABLED`, `CEREBRAS_API_KEY` | Optional Cerebras route |
| `PROVIDER_ORDER` | JSON array of route identifiers |
| `METRICS_API_KEY` | Separate secret protecting metrics with `X-Metrics-Key` |
| `TOTAL_REQUEST_TIMEOUT_SECONDS` | Shared routing budget, default 20 seconds |

Generate random keys in a password manager; do not use example placeholders.
See [.env.example](.env.example) for phase timeouts, token limits, queue/body limits,
and all configuration names. The API starts on loopback:

```powershell
& $Python main.py
```

In another terminal, provision `ROUTER_API_KEY` securely in that shell's environment
(the server's `.env` is not automatically loaded by PowerShell), then call:

```powershell
$headers = @{ 'X-API-Key' = $env:ROUTER_API_KEY }
$body = @{ messages = @(@{ role = 'user'; content = 'Explain circuit breakers.' }); temperature = 0.3 } | ConvertTo-Json -Depth 5
$reply = Invoke-RestMethod http://127.0.0.1:8000/v1/chat/completions -Method Post -Headers $headers -ContentType 'application/json' -Body $body
$reply.choices[0].message.content
$reply | Select-Object provider_used, model, fallback_used, latency_seconds, usage, routing_diagnostics
```

Supported roles are `system`, `user`, and `assistant`. Temperature is 0–2;
`max_tokens` is bounded by configuration. Extra fields and streaming are rejected.
`latency_seconds` describes the selected provider attempt, not the whole client
request. Missing upstream usage is estimated and marked; cost is always an estimate.
API schema: `http://127.0.0.1:8000/docs` and [API guide](docs/api.md).

## Security and operational boundaries

- Chat requires `X-API-Key`; a shared key is not per-user identity or a spending cap.
- Health endpoints are public and minimal. Readiness checks configuration, not
  actual provider connectivity or key validity.
- Set `METRICS_API_KEY` before public exposure; unset preserves public metrics
  for compatibility. Also restrict metrics through private monitoring ingress.
- Provider and dashboard HTTP clients disable redirects and environment proxy
  inheritance. Remote base URLs require HTTPS; HTTP is allowed on loopback.
- Error bodies, keys, authorization headers, and prompts are omitted from
  application error diagnostics/logs. Metrics use bounded labels.
- Rate limits, concurrency, queues, body limits, and circuit state are process-local.
  Multiple replicas need an explicit shared-state design.
- The dashboard holds a router key server-side. Anyone who can access it can use
  that key: bind it locally or require authenticated HTTPS ingress.

See [threat model](docs/threat-model.md) and [deployment preparation](docs/deployment-readiness.md).

## Optional dashboard

The CI dependency set above includes Streamlit. Configure the dashboard server:

```powershell
$env:DASHBOARD_ROUTER_URL = 'http://127.0.0.1:8000'
# Provision DASHBOARD_API_KEY and DASHBOARD_METRICS_API_KEY securely.
# ROUTER_API_KEY/METRICS_API_KEY are server-side configuration fallbacks.
& $Python -m streamlit run dashboard.py --server.address 127.0.0.1
```

All inference goes through the router. The interface distinguishes completed,
failed, and truncated output; shows observed provider/model/usage/diagnostics;
and separates **session history**, **response diagnostics**, and **server-wide
Prometheus snapshots**. Missing values stay missing. See [dashboard guide](docs/dashboard.md).

## Docker startup

Docker uses the committed **Linux/Python 3.12** `requirements-linux.lock`.
A fresh checkout can build without local `.locks/` files or a resolver installed:

```powershell
docker build --pull -t ai-router:local .
docker run --rm --name ai-router --env-file .env -p 127.0.0.1:8000:8000 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m --cap-drop ALL --security-opt no-new-privileges --memory 512m --cpus 1 --stop-timeout 50 ai-router:local
```

Use Linux containers. The image pins its Python base digest, installs the runtime
hash lock, runs UID/GID 10001, and excludes the dashboard and secrets. It checks
`/health/live`; configure orchestrator readiness separately.
`docker stop --time 50 ai-router` allows the 45-second Uvicorn graceful shutdown.
No HTTPS proxy or public dashboard is installed by this image.
For reviewed lock updates and exact Render service/environment settings, follow
the [Render deployment guide](docs/render.md). CI checks the lock by resolving
twice and builds the image from a fresh Git checkout.

## Verification and evidence

```powershell
& $Python -m pytest -v --cov=app --cov=config --cov=main --cov-report=term-missing
& $Python -m ruff check app config main.py dashboard.py test_failover.py scripts tests benchmarks
& $Python -m mypy
& $Python -m compileall app config main.py dashboard.py test_failover.py scripts benchmarks
& $Python -c "import main"
& $Python -m pip check
& $Python -m pip_audit -r .locks/runtime.txt --no-deps --disable-pip --strict
& $Python -m pip_audit -r .locks/ci.txt --no-deps --disable-pip --strict
git diff --check
```

Generate Windows scratch locks before a Windows locked installation. Switching
scratch targets does not replace the committed Linux deployment lock.
Audit both target sets in their respective fresh environments.
All known advisories block CI regardless of severity; feed errors fail closed.
Mypy covers three declared files, not the entire dynamic router/dashboard.
Normal tests use deterministic mocks and never call real providers. The opt-in
`scripts/nvidia_smoke.py` requires real credentials and may consume quota.

The Checkpoint E baseline passed **238 tests on Windows and Linux/amd64 with 97%
reported coverage**, strict dependency audits, and container runtime probes.
Checkpoint F results and scope are recorded in [verification](docs/verification.md).
Hosted GitHub Actions has not been executed as part of local verification.

## Benchmarks, case study, and limitations

The [Locust procedure](benchmarks/README.md) uses a local HTTP mock and retains
CSV/JSON observations. [Recorded results](docs/benchmark-results.md) distinguish
local mock throughput from live inference and deployed infrastructure. They are
not provider speed, production capacity, cost, or availability claims.

- [Engineering case study](docs/case-study.md)
- [Architecture and trust boundaries](docs/architecture.md)
- [Local demonstration](docs/demo.md)
- [Operations and failure classification](docs/operations.md)
- [Release/deployment runbook](docs/deployment-readiness.md)

Limitations include no streaming/tool-call output, no persistent/shared limit or
breaker state, no built-in TLS or Streamlit viewer authentication, and no production
deployment. Live provider/model compatibility requires an explicitly authorized
probe. HTTPS ingress, protected dashboard/metrics, OS-image scanning, hosted CI,
and staging acceptance remain deployment responsibilities.
