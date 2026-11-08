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

## Staging deployment

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
