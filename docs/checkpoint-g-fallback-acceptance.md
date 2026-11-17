# Checkpoint G fallback acceptance — 2026-11-17

The user reports successful live production inference, automatic default routing,
API authentication and protected metrics. Those results were not rerun here.
This acceptance exercises deterministic failure injection without production changes.

Existing `test_provider_order_and_reasoning_fallback` and
`test_nvidia_is_first_fallback_after_groq_failure` cover actual HTTPX upstream
request construction and response validation, but invoke the provider client
directly. `test_api_terminal_error_preserves_detail_and_adds_structured_diagnostics`
covers the FastAPI terminal-error path. They did not establish successful fallback
through the FastAPI endpoint.

Added `tests/test_api.py::test_api_checkpoint_g_fallback_acceptance` using the
existing HTTPX ASGITransport/MockTransport tooling. It runs application lifespan,
authentication, middleware, limits, routing, HTTP request/response handling,
strict upstream validation and public response serialization. Only upstream
transport is mocked; no router methods or validators are stubbed.

| Parameterized test case | Result | Selection / diagnostics |
| --- | --- | --- |
| `nvidia_success` | PASS | Fast 503 → NVIDIA success; `server_error` |
| `groq_reasoning_after_nvidia_503` | PASS | Fast 503 → NVIDIA 503 → Reasoning success; two `server_error` failures |
| `groq_reasoning_after_reasoning_only` | PASS | Fast 503 → NVIDIA reasoning-only → Reasoning; `server_error`, `unusable_output` |
| `groq_reasoning_after_model_mismatch` | PASS | Fast 503 → NVIDIA wrong model → Reasoning; `server_error`, `model_mismatch` |
| `groq_reasoning_after_malformed_response` | PASS | Fast 503 → NVIDIA malformed content → Reasoning; `server_error`, `invalid_response` |

Every case asserts exact attempt order, selected provider/model, HTTP 200,
`fallback_used=true`, diagnostic categories and attempted/selected flags, and
visible assistant content. Fast's retryable failure increments circuit health.
The test captures actual router log records, including structured fields, and
asserts synthetic router/provider credentials, private reasoning and prompt
markers appear in neither response bodies/headers nor logs. Separate reasoning
metadata is stripped from successful responses. Mock fallback is disabled.

Targeted API module: **13 passed, 0 failed**, including all five acceptance cases.
Complete suite: **366 passed, 0 failed**, 97% application coverage.
Evidence: `.locks/g-acceptance-full.log` and `.locks/g-acceptance-coverage.xml`.
Ruff, configured mypy (3 files), compilation, pip check and git diff --check passed.
No runtime application changes, production configuration edits, provider disabling,
authenticated live requests, merges or deployments were performed.
