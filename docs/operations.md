# Operational Runbook

## Health checks

- `GET /health/live`: process liveness; public and unauthenticated.
- `GET /health/ready`: readiness based on authentication configuration and eligible provider/fallback configuration.
- `GET /metrics`: Prometheus-format application metrics.

## Smoke test

```powershell
Invoke-RestMethod https://router.example.com/health/live
Invoke-RestMethod https://router.example.com/health/ready
```

Use an `X-API-Key` from the platform secret store for a chat-completion smoke test. Do not paste secrets into terminal history or logs.

## NVIDIA NIM configuration

The default remote route is Groq fast, NVIDIA NIM, then Groq reasoning. Set
`NVIDIA_API_KEY` in your secret store. `NVIDIA_ENABLED=false` removes NVIDIA from
the route. Cerebras requires `CEREBRAS_ENABLED=true` and `cerebras_primary` in
`PROVIDER_ORDER`. Provider order and allowlists use JSON arrays in environment variables.

`NVIDIA_BASE_URL` defaults to `https://integrate.api.nvidia.com/v1`.
`NVIDIA_DEFAULT_MODEL` defaults to `nvidia/nemotron-3.5-lightning-30b-a3b` and must
be included in `NVIDIA_ALLOWED_MODELS` to serve requests without an explicit model.
An explicit model is sent unchanged only to a compatible provider. A model outside
the configured model set is rejected with HTTP 422; a configured model without an
eligible working provider results in the existing generic HTTP 503 response.

`NVIDIA_DEFAULT_MAX_TOKENS=4096` applies when clients omit `max_tokens`.
Explicit budgets are capped at `NVIDIA_MAX_OUTPUT_TOKENS=16384`; all requests
are validated against `MAX_OUTPUT_TOKENS=16384`. Existing non-NVIDIA defaults
remain 1024. NVIDIA reasoning and visible output share the generation budget,
so raise the budget when reasoning exhausts it. Actual upstream usage is retained,
including internally consumed tokens. Separate reasoning fields are omitted from
the public response. Missing or empty visible content fails over through the
`unusable_output` path, subject to model eligibility and the total deadline.
The existing phase timeouts and `TOTAL_REQUEST_TIMEOUT_SECONDS` still apply;
increasing a token budget does not extend the deadline.
See [NVIDIA's reasoning-model guidance](https://docs.nvidia.com/nim/large-language-models/1.15.0/reasoning-model.html)
for model-specific behavior and generation-budget examples.

The opt-in live probe is `python scripts/nvidia_smoke.py`. It makes a real request
only when invoked directly, prints status only, and is never run by pytest or CI.
Normal tests use mocked HTTP transport and do not require NVIDIA credentials.

## Staging deployment steps

1. Build an immutable image tagged with the commit SHA.
2. Deploy that image to an isolated staging environment with production-like resource limits and a staging secret set.
3. Wait for the container health check and `/health/ready` to pass.
4. Run the smoke tests and review request, provider-failure, latency, and circuit-state metrics.
5. Promote the identical image digest to production only after staging passes.

## Rollback

1. Stop promotion when the readiness or smoke test fails, error rate increases, or latency breaches the service objective.
2. Redeploy the previously known-good image digest with its prior configuration revision.
3. Verify `/health/live`, `/health/ready`, and one authenticated smoke test.
4. Preserve logs and metrics for investigation; do not log or export secrets or prompt content.

## Resource limits

Set CPU, memory, replica, request-concurrency, and upstream-connection limits in the deployment platform. The values depend on measured workload and provider latency; this repository does not claim universal defaults.

## Failure classification and circuit health

The router continues to the next eligible provider after a classified failure;
it does not retry the same provider within a request or extend the total deadline.
Provider/model eligibility and explicit-model constraints always apply.

| Category | Circuit-breaker effect |
| --- | --- |
| `connection_error`, `timeout`, `server_error` (HTTP 5xx) | Increment availability/reliability failure count |
| `rate_limited` (HTTP 429) | Open immediately; honor capped Retry-After cooldown |
| `invalid_response` (malformed JSON/structure, invalid usage) | Increment reliability failure count |
| `unusable_output` (empty/reasoning-only/unsupported non-text result) | No health failure |
| `model_mismatch` | No health failure; check model/alias configuration |
| `auth_error` (HTTP 401/403), `provider_configuration_error` | No health failure |
| `unsupported_model` (HTTP 404), `invalid_request` (other client errors) | No health failure |
| `missing_credentials`, `model_not_supported`, `fallback_disabled`, `deadline_exceeded` skips | No health failure |
| `circuit_open` | Skip without attempting HTTP |

HTTP 408 is classified as a timeout. HTTP 404 can also indicate an incorrect
endpoint; inspect provider configuration when it appears as `unsupported_model`.
The router does not parse raw error bodies to infer more specific client errors.

Non-health failures preserve existing failure counts. If encountered during a
half-open recovery probe, they release the probe and retain the prior unhealthy
state for another recovery interval without adding a failure. Caller cancellation
also releases a probe. A validated completion within the deadline resets health.
Repeated unusable outputs can therefore continue to trigger failover without
opening the provider circuit; use the diagnostic category to investigate model
budgets or suitability. A `length` completion with visible text is accepted and
can be truncated; this checkpoint does not evaluate factual answer quality.
