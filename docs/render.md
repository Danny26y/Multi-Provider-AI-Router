# Render deployment preparation

The API image builds from a fresh Git checkout using the tracked
`requirements-linux.lock`. No `.env`, Windows lock, development lock, or local
cache is needed in the Docker context. The lock uses Python 3.12, uv 0.13.4,
and the unchanged resolution cutoff `2026-11-08T00:00:00Z`.
Docker retains `pip install --require-hashes`; it installs reviewed versions
without resolving a new dependency graph during deployment.

## Service settings

These settings describe a future manual deployment; this change does not create
or deploy a Render service. After this fix is reviewed, committed, and merged:

| Render setting | Value |
| --- | --- |
| Service type | Web Service |
| Repository | `Danny26y/Multi-Provider-AI-Router` |
| Branch | `main`, containing the reviewed lock fix |
| Language/runtime | Docker |
| Root Directory | Repository root; leave blank |
| Dockerfile Path | `./Dockerfile` |
| Docker build context | Repository root (`.`) |
| Docker Command override | Leave blank; use the existing Dockerfile CMD |
| Health Check Path | `/health/ready` |
| Auto-Deploy | Off while reviewing and performing manual acceptance |
| Plan/resources | Select explicitly after reviewing current pricing and resource limits |

The container listens on `0.0.0.0:8000`. Set `PORT=8000` in Render to match it;
this variable does not change the Dockerfile's fixed listener. Do not use
`python main.py` as an override: that local command binds to loopback.
Docker's internal liveness probe remains `/health/live`. Render's readiness
probe validates eligible configuration, not provider key validity/connectivity.

See Render's [Docker configuration](https://render.com/docs/docker),
[port binding](https://render.com/docs/web-services#port-binding),
[health checks](https://render.com/docs/health-checks), and
[automatic deploy settings](https://render.com/docs/deploys#automatic-deploys).
No hosting tier is assumed permanently free.

## Environment variables

Add secrets in the service's **Environment** page or a linked environment group.
Do not commit a filled `.env`, include keys in URLs, or reference secrets through
Docker `ARG`/`RUN` instructions. Render makes environment values available during
build as well as runtime; this Dockerfile does not consume secret build arguments.
See [Render's environment guide](https://render.com/docs/configure-environment-variables).

| Variable | Requirement |
| --- | --- |
| `ROUTER_API_KEY` | Strong random secret; clients send it as `X-API-Key` |
| `METRICS_API_KEY` | A different strong secret; required for protected public `/metrics` access using `X-Metrics-Key` |
| `GROQ_API_KEY` | Groq credential for fast and reasoning routes |
| `NVIDIA_API_KEY` | NVIDIA NIM credential for NVIDIA fallback |
| `CEREBRAS_API_KEY` | Optional; only with Cerebras explicitly enabled |

Configure at least one provider credential. Supply both Groq and NVIDIA keys for
the default Groq fast → NVIDIA → Groq reasoning chain. No `RENDER_API_KEY` is
required inside the application. Set the following non-secret variables:

```dotenv
APP_ENV=production
ENABLE_MOCK_FALLBACK=false
NVIDIA_ENABLED=true
CEREBRAS_ENABLED=false
PORT=8000
```

Other defaults, including NVIDIA URL/model/allowlist and token budgets, are in
[.env.example](../.env.example). The API image excludes Streamlit. A separately
hosted dashboard would need `DASHBOARD_ROUTER_URL` set to the HTTPS API URL,
`DASHBOARD_API_KEY`, `DASHBOARD_METRICS_API_KEY`, and authenticated viewer access.
The server-held router credential does not authenticate dashboard visitors.

## Lock maintenance and pre-deployment checks

With uv 0.13.4 on PATH, from the repository root:

```powershell
python scripts/lock_dependencies.py --platform linux --update-runtime-lock
python scripts/lock_dependencies.py --platform linux --check-runtime-lock
python -m pip_audit -r requirements-linux.lock --no-deps --disable-pip --strict
docker build --pull -t ai-router:review .
```

Update mode is for reviewed dependency changes, not a Render build hook. Check
mode compares the committed bytes with two independent Linux resolutions; a
mismatch or network/resolver failure blocks CI. Windows target or benchmark flags
are rejected in either release mode. Local `.locks/` output remains ignored and
does not enter Docker's build context. Only the Linux release artifact is newly
tracked, with LF line endings enforced through `.gitattributes`.

CI installs hash-locked development dependencies, retains the strict all-advisory
audit policy, verifies the committed runtime lock, and clones the tested commit
into a clean directory for the Docker build. A missing committed lock cannot be
masked by local or CI-generated `.locks/runtime.txt`.

Before manual promotion, inspect hosted CI, confirm health, wrong-key rejection
and protected metrics, then perform a separately authorized provider smoke test
with conservative quota/token budgets. Do not expose an unprotected dashboard.
Record the source SHA, lock, image digest, and configuration revision for rollback.
OS-image scanning and staging acceptance remain deployment responsibilities; see
the [release runbook](deployment-readiness.md).

## Local verification of this preparation

Verified on 2026-11-12, before committing or deploying:

- Windows: 292 deterministic tests passed; 97% reported application coverage.
  Tests used an isolated environment with dotenv loading disabled.
- Linux/amd64 offline runtime profile: 286 passed, two skipped (Streamlit UI
  module and Git-dependent evidence check are unavailable in the slim image).
- Ruff, the configured mypy scope, compilation/import, and dependency consistency
  checks passed. Runtime, CI, and benchmark audits reported zero known findings.
- Linux lock regeneration matched twice on both Windows and native Linux.
  Existing runtime package pins and distribution hash sets were unchanged.
- Docker built from a fresh Git-index checkout of the proposed changes, with no
  local `.locks/` directory. The real Git index and HEAD were left untouched.
- Container probes passed non-root execution, read-only operation, public
  liveness, unconfigured readiness rejection, API authentication, protected
  metrics, sanitized terminal errors, and graceful shutdown with exit code zero.

Hosted CI for this change and actual Render/provider acceptance remain pending
review and a future authorized deployment. No live provider calls were made.
