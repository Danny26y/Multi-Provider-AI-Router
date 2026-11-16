# Checkpoint G: production inference recovery preparation

Date: 2026-11-16. No merge, deployment, or Render environment change performed.
Production recovery is **not verified**. Tests below use mocked transports;
authenticated upstream and deployed inference remain release gates.

## Evidence and limits of the diagnosis

The reported deployed failure was `no_provider_available`: both Groq routes
reported `unsupported_model`, NVIDIA reported `unusable_output`, and production
correctly refused mock fallback at both 128 and 1024 requested tokens.

Confirmed code defects and request hazards:

- Every HTTP 404 was classified as `unsupported_model`, even a wrong endpoint
  or a non-JSON response. Previously the error body was discarded, so the
  existing diagnostics cannot establish model deprecation or account access.
  The fix checks only the bounded structured `error.code == model_not_found`;
  other 404s become `upstream_not_found`. Neither affects circuit health.
  A model-not-found error still does not distinguish absence from lack of access.
- Groq's [current model list](https://console.groq.com/docs/models) lists
  `openai/gpt-oss-20b` and `openai/gpt-oss-120b` as production models. The old
  Llama IDs are still listed as **Enterprise**; universal deprecation is not
  established. Defaults migrate to the verified GPT-OSS identifiers. Credential
  entitlement and the actual Render model/URL overrides must still be checked.
- NVIDIA requests omitted `stream`; the hosted
  [API reference](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-5-lightning-30b-a3b-infer)
  documents a streaming default. The client expects one JSON response. All
  requests now explicitly send `stream: false`. Streaming JSON parse failure
  would have been `invalid_response`, so this does not explain the reported
  `unusable_output` by itself.
- NVIDIA requests omitted thinking controls. Its documented default reasoning
  budget is 16384, exceeding both reported token caps. NVIDIA's
  [model guidance](https://docs.nvidia.com/nim/large-language-models/2.0.10/get-started/advanced/get-started-nemotron-3.5-lightning.html)
  explains that thinking consumes the shared generation cap and can end before
  any final text. The sanitized regression reproduces `content: null`,
  `finish_reason: length`, and 128 reasoning tokens out of 128 completion tokens;
  strict validation correctly yields `unusable_output` and real-provider fallback.
  This establishes the mechanism, **not the actual contents of Render's response**.

The precise NVIDIA production cause remains unconfirmed: `unusable_output`
can also mean empty/whitespace content or a non-text finish reason. Model
mismatch produces `model_mismatch`; malformed structures produce
`invalid_response`; transport timeouts produce `timeout`. No credentialed
response metadata was available to distinguish those production possibilities.
Do not present a synthetic fixture as a captured upstream response.

The configuration mitigation explicitly disables NVIDIA thinking by default,
using `chat_template_kwargs.enable_thinking: false`. Operators can opt in with
`NVIDIA_ENABLE_THINKING=true`; the hosted `reasoning_budget` is capped at the
lesser of the configured budget and half the effective `max_tokens`. At one
token thinking is disabled. The caller's explicit cap is never increased.
These controls apply only to the named Nemotron model; alternate allowlisted
models receive no Nemotron-specific parameters. Tiny output budgets can still
fail and must never be converted into fabricated success.

GPT-OSS requests use documented `reasoning_effort: low`; they do not send
`reasoning_format`, which those models do not support according to
[Groq's reasoning documentation](https://console.groq.com/docs/reasoning).
Public responses retain only assistant role and visible content. Separate
reasoning fields are discarded. Embedded `<think>` traces are rejected as
unusable instead of being extracted or exposed. Strict model identity, usage,
choice structure and finish-reason checks remain enabled.

## Deadline decision

A ten-second read wait is aggressive for non-streaming reasoning inference.
The previous shared 20-second deadline could leave little opportunity to try
Groq Reasoning after NVIDIA. New defaults are a 60-second shared deadline
(maximum 180), NVIDIA 30-second read timeout (maximum 120), and NVIDIA
35-second complete attempt cap (maximum 120). Every attempt/phase is clamped
to the remaining shared deadline. For the three-provider default chain,
roughly ten seconds on Groq Fast plus 35 on NVIDIA leaves roughly 15 for
fallback; these are budgets, not latency guarantees.

The NVIDIA attempt cap covers the whole response stream, including slow-drip
responses. Its timeout cancels and closes the exchange before fallback.
Existing breaker admission ownership/finally cleanup, neutral response failures,
rate-limit cooldowns, body caps, concurrency slots, and per-client rate limits
are preserved. Longer waits hold concurrency slots longer; monitor saturation
and queue rejection metrics. No latency benchmark justifies further increases.
Keep the defaults until live evidence supports tuning. Dashboard wait becomes
75 seconds and container graceful shutdown 75 seconds; use stop grace 85 seconds.
If increasing the total budget above 60, also raise downstream/client/shutdown
grace explicitly. The dashboard supports at most 120 seconds.

## Render configuration for a separately approved deployment

Explicitly replace existing overrides; changing Python defaults does not replace
Render environment values. Capture the old configuration securely first.

```dotenv
APP_ENV=production
ENABLE_MOCK_FALLBACK=false
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_FAST_MODEL=openai/gpt-oss-20b
GROQ_REASONING_MODEL=openai/gpt-oss-120b
NVIDIA_ENABLED=true
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_DEFAULT_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
NVIDIA_ALLOWED_MODELS=["nvidia/nemotron-3.5-lightning-30b-a3b"]
NVIDIA_ENABLE_THINKING=false
NVIDIA_REASONING_BUDGET=1024
NVIDIA_DEFAULT_MAX_TOKENS=4096
NVIDIA_MAX_OUTPUT_TOKENS=16384
NVIDIA_READ_TIMEOUT_SECONDS=30
NVIDIA_ATTEMPT_TIMEOUT_SECONDS=35
REQUEST_READ_TIMEOUT_SECONDS=10
TOTAL_REQUEST_TIMEOUT_SECONDS=60
CEREBRAS_ENABLED=false
PROVIDER_ORDER=["groq_fast","nvidia","groq_reasoning","mock_fallback"]
```

Keep router/provider/metrics secrets in Render's secret store; never paste them
into evidence, CLI arguments, PRs, or logs. Do not add broad model aliases to
mask mismatches. If separately hosting the dashboard, set
`DASHBOARD_REQUEST_TIMEOUT_SECONDS=75`. Leave auto-deploy off during review.

## Explicit LIVE acceptance procedure

1. After approval, deploy the reviewed SHA and record its image digest and
   configuration revision. Readiness validates configuration, not provider access.
2. With the deployment's Groq credential, call
   `GET https://api.groq.com/openai/v1/models` using an in-memory bearer header.
   Record only presence/absence of the two configured GPT-OSS IDs. Check model
   permissions if absent; do not save the complete account response.
3. Set `SMOKE_ROUTER_URL=https://multi-provider-ai-router-1.onrender.com` and
   supply router/metrics credentials securely in the shell environment. Run
   `python -m scripts.production_smoke` from the repo root. This makes LIVE
   requests and consumes inference quota. It checks public liveness/readiness,
   invalid router key 401, anonymous metrics 401, authenticated metrics 200, and
   authenticated model-pinned Fast/NVIDIA/Reasoning completions. Each must have
   the expected provider/model and nonempty visible assistant text. The script
   prints only outcomes, never prompts, responses, keys, or reasoning.
4. Repeat NVIDIA model-pinned requests with explicit `max_tokens=128` and
   `1024`, then with the field omitted (4096 configured default). Record only
   HTTP status, selected provider, exact expected-model match boolean, finish
   reason, content-present boolean, token counters, and elapsed seconds.
   On failure, use a controlled direct upstream diagnostic with the same fixed
   public greeting. Record only content/reasoning-present booleans, finish
   reason and counters; never record reasoning text or raw response bodies.
   Compare thinking off against thinking on with the bounded reasoning budget.
   `python -m scripts.nvidia_smoke` provides a strict live 128-token probe through
   the same provider request/validation path; HTTP 200 alone cannot pass it.
5. In an isolated staging instance of the same SHA, deliberately set Groq Fast
   to an unavailable model and send an **unpinned** request. Require NVIDIA
   success, a failed first diagnostic, `fallback_used=true`, and no mock output.
   Then force NVIDIA failure too and require Groq Reasoning selection. Restore
   configuration. Do not inject provider faults into production or use a pinned
   model to test cross-model fallback (pinning intentionally forbids substitution).
6. In staging, remove provider credentials or use controlled unavailable test
   endpoints and request inference with valid router auth. Require 503 and
   `no_provider_available`/`deadline_exceeded` with only safe diagnostics; no
   upstream text, secrets, private prompts, or reasoning. Confirm production
   mock fallback remains disabled. Restore settings and recheck readiness.
7. Record live and mocked results separately. Production recovery requires a
   successful authenticated completion from the deployed SHA, plus acceptance
   of the intended provider/fallback paths. Health 200 or local tests cannot
   establish recovery.

## Files and verification

| Files | Purpose |
| --- | --- |
| `config/settings.py`, `.env.example` | Groq defaults, bounded NVIDIA policy and deadline configuration |
| `app/services/provider_client.py` | Explicit JSON wire request, reasoning controls, per-attempt deadline, bounded structured 404 handling |
| `app/services/response_validation.py` | Reject embedded reasoning traces while preserving strict wire validation |
| `app/services/dashboard_client.py`, `Dockerfile` | New safe diagnostic and aligned client/shutdown waits |
| `scripts/nvidia_smoke.py`, `scripts/production_smoke.py` | Opt-in live validation without printing response content |
| `tests/test_checkpoint_g.py`, existing dashboard/response tests | Regression coverage and updated timeout/404 expectations |
| README, API/operations/deployment docs, `docs/render.md`, this file | Configuration, evidence, acceptance and rollback guidance |
| `.gitignore`, `.dockerignore` | Exclude isolated local verification environment |

Verification on Windows/Python 3.12.8, with dotenv loading disabled:

| Check | Result |
| --- | --- |
| Full deterministic pytest suite | **323 passed, 0 failed**, including 31 new Checkpoint G cases; 97% coverage |
| Ruff | Pass, 0 errors |
| Configured mypy scope | Pass, 3 source files |
| Application/scripts compilation | Pass |
| Installed dependency consistency (`pip check`) | Pass |
| pip-audit: committed Linux runtime, Linux CI, Windows CI verification, benchmark locks | All 4 pass; 0 known vulnerability findings |
| Docker build | Blocked before build: Docker Desktop Linux engine named pipe is absent |
| LIVE existing Render liveness/readiness | 2 passed, HTTP 200/200 |
| LIVE existing Render invalid router key/anonymous metrics | 2 passed, HTTP 401/401 |
| LIVE authenticated inference/provider-model list/authenticated metrics | Not run: required credentials unavailable |
| NVIDIA/gateway probe missing-credential guard | Both report SKIPPED with nonzero exit; no inference made |

The isolated verification environment was installed with hashes from the existing
Windows CI lock. The Linux CI lock alone cannot install on Windows because it
omits the Windows-only colorama hash. No dependency files were changed.
Local logs/audit JSON remain ignored under `.locks/g-*`. Hosted Linux CI and
Docker acceptance are separate from these local results. The four LIVE public
checks exercised the existing deployment, not this branch's code.

## Rollback

Before promotion, retain the previous reviewed Render deployment/image digest
and a secret-store snapshot of its environment. If inference, security, latency,
or saturation regresses, manually roll back to that image and restore the
matching configuration revision together. Keep production mock fallback off.
Repeat liveness, readiness, key rejection and authenticated inference checks.
The previous deployment already fails inference, so rollback restores the prior
operational state rather than guaranteeing service recovery. Do not silently
route to mock content. A later code revert must be reviewed through a PR.
