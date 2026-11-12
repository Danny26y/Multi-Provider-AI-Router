# Checkpoint E: release and deployment preparation

No services have been deployed. Do not promote an image until its dependency,
security, and container checks have succeeded in a connected environment.

## Local verification after remediation

This section records Checkpoint E. See [Checkpoint F verification](verification.md)
for the later documentation, harness, and final review results.

- Fresh hash-locked Windows Python 3.12.8 and Linux/amd64 Python 3.12.16
  environments: **238 passed, 97% coverage each**. Linux was executed in Docker,
  not inferred from Windows results. Both environments passed `pip check`, Ruff,
  the configured three-file mypy scope, compilation, and `import main`.
- Strict runtime and CI dependency audits: **no known vulnerabilities** in both
  environments. No advisories were ignored and the security gate is unchanged.
- Three additional fresh Windows UI-test processes with coverage: **5 passed
  each**. AppTest already waits for script completion; cold rendering-library
  imports now run in module fixture setup. The interaction timeout remains 15
  seconds, without sleeps, retries, or suppressed exceptions.
- Linux hash locks generated successfully; API image build with those locks
  passed. Runtime probes verified UID/GID 10001, root-owned application source,
  no dashboard or dotenv file, liveness, unconfigured readiness 503, rejected
  missing/wrong authentication, authenticated metrics, sanitized no-provider
  errors, and graceful shutdown (exit 0). The runtime probe used no network,
  published ports, or provider credentials.
- Local evidence is retained under ignored `.locks/remediation-windows/`,
  `.locks/remediation-linux/`, and `.locks/remediation-image/`. The active
  `.locks/runtime.txt` and `.locks/ci.txt` are Linux locks after verification;
  regenerate Windows locks before a Windows locked installation.
- Hosted GitHub Actions and real HTTPS ingress/dashboard authentication: not run.
  No provider requests, cloud deployments, account creation, or charges were made.

Hosted CI and deployment acceptance remain outstanding. Local tests do not
certify production ingress, provider connectivity, capacity, or OS-image security;
no OS-package vulnerability scanner was run. Review the generated release
artifacts and provision protected ingress and secrets before deployment.

## Change inventory

- Runtime/security: `main.py`, `config/settings.py`, `app/core/body_limit.py`
  (new), `app/core/metrics.py`, `app/core/circuit_breaker.py`,
  `app/services/provider_client.py`, and `app/services/dashboard_client.py`.
  Remediation also normalizes import spacing in `app/core/logging.py`,
  `app/schemas/requests.py`, and `tests/test_dashboard_client.py`.
- Release inputs: `Dockerfile`, `.dockerignore`, `.github/workflows/ci.yml`,
  `.gitignore`, `.env.example`, `pyproject.toml`, `requirements.txt`,
  `requirements-dev.txt`, `requirements-dashboard.txt`, and
  `scripts/lock_dependencies.py` (new).
- Regression tests: `tests/test_operational_hardening.py` (new),
  `tests/test_response_validation.py` (stronger deadline assertions), and
  `tests/test_provider_client.py` (import ordering only), and
  `tests/test_dashboard_ui.py` (cold rendering dependency setup).
- Documentation: `README.md`, `docs/dashboard.md`, `docs/threat-model.md`, and
  this new runbook. No benchmark work, cloud deployment, or merge is included.

## Dependency and CI policy

Python 3.12 is the supported release interpreter. Direct dependency inputs are
exact pins in `requirements.txt`, `requirements-dev.txt`, and
`requirements-dashboard.txt`. The optional dashboard no longer installs unused
Plotly or Requests; its networking uses the runtime HTTPX dependency. Benchmarks
remain optional. Checkpoint F adds `--benchmark` to generate `.locks/benchmark.txt`,
constrained by the CI lock, and audits that optional set without installing Locust
in the API image or requiring it for normal pytest collection.

Use the pinned resolver `uv==0.13.4` with the fixed resolution cutoff
`2026-11-08T00:00:00Z` and explicit Python/platform target. This is an **as-of
resolution strategy**. `scripts/lock_dependencies.py` generates `.locks/runtime.txt`
and `.locks/ci.txt` with exact transitive versions and distribution hashes. CI
installs only these generated locks using `--require-hashes` and archives them.
The CI set includes the optional dashboard so headless UI tests execute, not skip.
The API image instead installs the reviewed, committed `requirements-linux.lock`;
it never depends on ignored local output being present in a fresh checkout.

```powershell
python -m pip install uv==0.13.4
python scripts/lock_dependencies.py --platform windows
python -m venv .venv
.venv/Scripts/python -m pip install --require-hashes -r .locks/ci.txt
```

To update the deployable Linux runtime artifact, use the existing pinned resolver:

```powershell
python scripts/lock_dependencies.py --platform linux --update-runtime-lock
python scripts/lock_dependencies.py --platform linux --check-runtime-lock
```

Review and commit `requirements-linux.lock` with runtime-input/policy changes.
Check mode resolves twice into temporary ignored files and compares both results
byte-for-byte with the committed artifact; it never overwrites that artifact.
Canonical LF output excludes uv's path-dependent header and annotations. CI
audits this exact file, then builds from a separate fresh Git checkout, without
`.locks/`. Windows/development/benchmark scratch locks remain ignored. See the
[Render settings](render.md) for the hosted Docker build.

Do not feed a Windows lock to Docker. A release consists of the reviewed source
SHA, committed Linux runtime lock, generated development locks,
resolver/cutoff, audit reports, base-image digest, and final image digest. Reuse
the archived locks when rebuilding a release; do not re-resolve them. Package
yanks or index availability can affect fresh as-of resolutions, so the archived
hash locks are the authoritative release artifacts. Never silently fall back to
unlocked installation if generation fails. Review pin/cutoff changes explicitly.

The baseline environment's FastAPI 0.111.0 requires Starlette <0.38.0, a range
with [published advisories](https://github.com/Kludex/starlette/security/advisories/GHSA-f96h-pmfr-66vw). The release inputs update that framework pair to
FastAPI 0.162.2 / Starlette 1.10.0. Fresh locked Windows and Linux suites now
verify this framework migration. Remediation uses python-dotenv 1.2.2,
Streamlit 1.66.1, Pillow 13.0.0, and uv 0.13.4. Streamlit 1.66.1 is the first
stable release after 1.56.0 whose published requirements permit Pillow 13
(`pillow>=9.2,<14`); no dependency overrides were used. Pandas remains 3.0.3.
All selected releases predate the unchanged resolution cutoff. Bare Uvicorn is sufficient
for this non-streaming HTTP API and avoids unnecessary standard-extra dependencies.
Rerun the suite and vulnerability scans in hosted CI before release.

CI checks Ruff lint (including import ordering) across application/config/dashboard/scripts/
tests. Whole-repository formatter normalization is not a release gate; it would
create unrelated formatting churn in the earlier checkpoints. Supported mypy scope is explicitly `app/schemas/requests.py`,
`config/settings.py`, and `app/core/logging.py`; legacy dynamic routing/UI and
tests are not advertised as fully typed. Expanding this scope is future work.
CI also checks compilation/import, pytest including UI, dependency consistency,
and Docker build/runtime probes. Provider credentials are not CI secrets.

Security severity policy: **all known dependency vulnerabilities block CI**,
including low, medium, high, critical, or unknown severity. `pip-audit` does not
provide a uniform CVSS severity field, so CI does not guess severity from IDs or
silently exempt unknown findings. No advisory allowlist is included. Service or
feed failures also fail the check (`--strict`); review an explicit, expiring
exception separately if an advisory cannot yet be fixed. Audit runtime and CI
sets, retain JSON reports, and rerun before promotion. This is dependency
advisory scanning, not a substitute for OS image scanning or penetration testing.

CI has read-only repository permissions, bounded job duration, and no deployment
credentials, image push, or cloud deployment step. Docker is built locally in
the runner and tested with no provider keys. Install/network failures remain
visible failures. The Dockerfile pins the verified Python 3.12 slim Bookworm
base to `sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df`.
CI records that pinned manifest. A digest also freezes OS fixes: review and test
base-image refreshes deliberately, with an OS-image security scan before release.

## Runtime metrics

`ai_router_http_requests_total` / HTTP duration: one record per HTTP request,
including rejected requests. Method/path labels are finite allowlists with
`OTHER`/`unmatched` buckets. No raw URLs or client identities are metric labels.

`ai_router_completions_total{provider,outcome}`: exactly one terminal result per
routing invocation. Failed/cancelled requests use provider `none`; eligible
provider skips are not completion results. Outcomes distinguish success,
truncation, local mock, failure, cancellation, and deadline exhaustion.

`ai_router_provider_selections_total` and `ai_router_fallback_selections_total`:
increment only for the selected validated response, not every fallback attempt.
`ai_router_provider_failures_total`: one classified failure per attempted provider.
Provider duration includes failed attempts; routing duration covers the complete
chain. Consequently HTTP request, routing request, and attempt totals have
different denominators and must not be added together.

`ai_router_tokens_total{provider,kind,source}` counts selected response token
usage only. `source` separates reported/estimated counts; mock usage is excluded.
`kind=total` already includes prompt plus completion; do not sum all three kinds.
`ai_router_circuit_transitions_total` increments only on actual status changes;
the state gauge is a snapshot, not a transition counter.

HTTP in-flight, chat in-flight, chat queued, configured concurrency limit, and
rate/concurrency rejection counters expose per-process saturation. Request
latency histograms use fixed Prometheus buckets. All provider labels are bounded
to configured built-in route identifiers or `other`; no arbitrary model labels.
Workers and replicas do not share counters, circuit health, or limit state.

## Secrets and deployment prerequisites

Provision random, separate `ROUTER_API_KEY` and `METRICS_API_KEY` secrets using a
password manager/secret store (for example, generate 32 random URL-safe bytes
locally). Do not paste them into command arguments, image layers, source, or CI
logs. Set `APP_ENV=production`, `ENABLE_MOCK_FALLBACK=false`, and only intended
provider keys (`GROQ_API_KEY`, `NVIDIA_API_KEY`, optional `CEREBRAS_API_KEY`).
Review `PROVIDER_ORDER`, enable flags, NVIDIA allowlist/model/token budgets, phase
timeouts, and `TOTAL_REQUEST_TIMEOUT_SECONDS`. Provider endpoints require HTTPS;
HTTP is permitted only for loopback test upstreams. Redirects and automatic
environment proxy use are disabled in both HTTP clients. A custom corporate
proxy therefore needs a separate reviewed transport configuration.

Upstream success bodies are streamed with a configurable
`MAX_UPSTREAM_RESPONSE_BYTES` cap (default 4 MB, configuration ceiling 64 MB).
Oversized bodies are reliability failures and eligible for failover. Error
bodies are discarded without parsing. JSON decoding remains synchronous, so the
async timer cannot preempt it, but its input size is bounded and late completions
are rejected. Identity encoding is requested; HTTPX can still allocate a decoded
chunk before checking the cap if a server ignores that request and compresses a
response. The cap is not an absolute process-memory guarantee against a hostile
compressed upstream. Keep provider endpoints trusted and memory limits enforced.

`METRICS_API_KEY` enables `X-Metrics-Key` authentication on `/metrics`. Leaving it
unset preserves the previous public endpoint for compatibility; **set it before
public exposure** and also restrict network access. The dashboard accepts
`DASHBOARD_METRICS_API_KEY` (fallback `METRICS_API_KEY`) separately from its chat
key; no metrics key is sent to health or chat requests. The API key and metrics
key are absent from settings repr and sanitized in dashboard display mapping.
HTTP 422 retains the `detail` list envelope but sanitizes entries to a generic
body-validation message, location, and type; raw inputs and exception contexts
are omitted. Clients requiring the former detailed validation messages must
adapt. Valid chat request/response schemas and health contracts are unchanged.

Health probes stay unauthenticated and minimal: `/health/live` (200 while running),
`/health/ready` (200 only with eligible configuration, otherwise 503), `/health`
(compatibility liveness). Readiness does not contact a provider or validate its key.
An empty provider allowlist is ineligible, and mock readiness requires the mock
route to be present in the configured order as well as explicitly enabled.
Do not substitute readiness for liveness or restart a healthy container for a
provider quota incident. Use authenticated, explicitly opt-in staging inference
to establish real connectivity.

## Low-cost HTTPS deployment shape

Use one existing Linux VM or a small container host with persistent HTTPS ingress.
Assess its current price, billing limits, idle/sleep behavior, egress, disk, and
memory allowance before creating resources. No provider is assumed permanently
free. A starting **measurement budget**, not a capacity guarantee, is one vCPU
and 512 MiB for the API, plus separate headroom for ingress and an optional
dashboard (start with 1 GiB for Streamlit and measure). Shared free-tier memory
may be insufficient for the dashboard; omit it or access it locally by SSH tunnel.

Verify the committed Linux lock, then build an immutable image tagged with the source SHA:

```sh
python scripts/lock_dependencies.py --platform linux --check-runtime-lock
docker build --pull -t ai-router:REVIEWED_SHA .
docker image inspect ai-router:REVIEWED_SHA
```

Supply secrets using the host/platform secret mechanism. If using an env file,
keep it outside the repository, readable only by its owner, and exclude it from
backups/log exports. An illustrative local-host API container command is:

```sh
docker run -d --name ai-router --restart unless-stopped \
  --env-file /secure/router.env -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 512m --cpus 1 --stop-timeout 50 ai-router:REVIEWED_SHA
```

The image runs UID/GID 10001, uses exec-form Uvicorn startup, one worker, finite
server concurrency/keepalive, and 45 seconds for graceful shutdown. The host's
stop grace period must exceed that. A longer configured inference deadline also
requires reviewing shutdown grace and ingress timeouts. No shell/reloader is PID 1.
Lifespan shutdown closes the shared upstream HTTP client.

Terminate HTTPS with a maintained reverse proxy or platform ingress. Map a domain
and configure certificate issuance/renewal according to that ingress's current
documentation. Expose only HTTPS publicly, route to the loopback API, and deny
public `/metrics`; scrape it privately with `X-Metrics-Key`. Do not expose the
Docker socket or database/admin ports. Enforce additional body/connection limits
at ingress. Set its request timeout above the application's routing deadline.

The container ignores forwarded client headers by default. Behind a proxy, IP
rate limits may group all clients under the proxy address; do not blindly trust
arbitrary `X-Forwarded-For`. Configure trusted proxy ranges explicitly only after
reviewing the network topology. Shared API-key authentication is not per-user
identity, tenant isolation, or a spending cap. Limit state is in-process, with at
most `MAX_TRACKED_CLIENTS` retained clients and idle reclamation. Waiters expire
after `CONCURRENCY_QUEUE_TIMEOUT_SECONDS`; actual request bodies are bounded to
1 MB and body reads to `REQUEST_BODY_TIMEOUT_SECONDS`.
These inference limits apply after authentication; ingress must also limit
unauthenticated traffic. The server-wide connection cap can reject health probes
under extreme saturation, so preserve ingress/probe headroom and alert on overload.

## Protected dashboard

Run Streamlit bound to `127.0.0.1` for host-local access or an SSH tunnel. Keep
XSRF protection enabled. To publish it, first configure authenticated ingress
(OIDC/identity-aware proxy preferred, or HTTPS basic auth with a strong hashed
password) **before** forwarding traffic to Streamlit. Bind only to a private
interface and firewall its port. A random router key in dashboard environment
does not authenticate dashboard viewers: all allowed viewers act with that key.
Do not run the dashboard on an unprotected public listener. API images exclude
dashboard files and never start Streamlit. There is no public dashboard service
or automatic deployment in this repository.

## Quotas, acceptance, and rollback

Check each enabled provider's account/model quotas, rate limits, trial expiry,
token pricing, and billing limits in its current console. Reasoning tokens consume
output budgets. Failover can consume several providers' quotas on one request;
a router rate limit is not an upstream cost guarantee. Configure conservative
allowlists, token ceilings, key permissions, and billing alerts before live use.
Never run live-provider probes automatically in CI.

Before promotion: locked fresh install, advisory audit, full tests, lint, supported
type scope, image build/non-root/health checks, HTTPS verification, wrong-key
denial, protected metrics, protected dashboard, and one approved staging prompt.
Observe 5xx/rate-limit trends, routing latency, queue rejection, fallback, and
token-source labels. Record the image digest and configuration revision.

Rollback on readiness regression, error/latency deterioration, or unexpected
quota use: stop promotion, restore the prior image digest **and** configuration
revision, allow graceful shutdown, then check liveness, readiness, HTTPS, and key
rejection. Keep only sanitized metrics/logs for investigation. Do not rotate back
to a known-compromised key. Do not claim service capacity or cost savings without
measurement; benchmarking and broader documentation belong to Checkpoint F.
