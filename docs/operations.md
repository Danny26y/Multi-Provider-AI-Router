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
existing invalid-response path, subject to model eligibility and the total deadline.
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
