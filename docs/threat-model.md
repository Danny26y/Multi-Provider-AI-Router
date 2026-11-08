# Threat Model

## Assets

- Router API key and provider credentials
- User prompt content and provider responses
- Availability of the routing endpoint

## Controls in this repository

- Chat requests require `X-API-Key`; missing router configuration fails closed.
- Credentials are supplied through environment variables and excluded from Git and Docker build contexts.
- Inputs are constrained by role, size, token, temperature, and model validation.
- Logs and routing diagnostics exclude request content, API keys, headers, and upstream response bodies.
- The container runs as an unprivileged user and exposes a liveness health check.
- Client limits and circuit breakers reduce accidental or transient overload within one process.

## Deployment responsibilities

- Terminate HTTPS at a managed load balancer or reverse proxy.
- Store secrets in the deployment platform's secret manager, not in images, repository files, or CI logs.
- Restrict `/metrics` and diagnostic endpoints to trusted monitoring networks where needed.
- Apply network policies, image scanning, patching, and centralized log retention appropriate to the environment.

## Non-goals

This repository does not provide user identity management, tenant isolation, global distributed rate limiting, or a shared circuit-breaker store.
