# Threat Model

## Assets

- Router API key and provider credentials
- User prompt content and provider responses
- Availability of the routing endpoint

## Controls in this repository

- Chat requests require `X-API-Key`; missing router configuration fails closed.
- Metrics can require a separate `X-Metrics-Key` through `METRICS_API_KEY`; deployment must enable it and restrict ingress.
- Credentials are supplied through environment variables and excluded from Git and Docker build contexts.
- Inputs are constrained by role, size, token, temperature, and model validation.
- Logs and routing diagnostics exclude request content, API keys, headers, and upstream response bodies.
- The container runs as an unprivileged user and exposes a liveness health check.
- Client limits and circuit breakers reduce accidental or transient overload within one process.
- Actual body bytes (including chunked input), body-read duration, concurrency queue waits, and retained limiter identities are bounded.
- Unknown URL paths/methods are normalized for metrics/logging. Arbitrary correlation IDs and server-key echoes are rejected.
- Outbound clients reject redirects and ambient proxy configuration; provider URL configuration rejects embedded credentials and non-loopback plaintext HTTP.
- Validation errors are sanitized rather than echoing request bodies. Health endpoints stay public and minimal.

## Deployment responsibilities

- Terminate HTTPS at a managed load balancer or reverse proxy.
- Store secrets in the deployment platform's secret manager, not in images, repository files, or CI logs.
- Restrict `/metrics` and diagnostic endpoints to trusted monitoring networks where needed.
- Apply network policies, image scanning, patching, and centralized log retention appropriate to the environment.

## Non-goals

This repository does not provide user identity management, tenant isolation, global distributed rate limiting, or a shared circuit-breaker store.
