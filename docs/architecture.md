# Architecture

```text
Client
  -> HTTPS reverse proxy or load balancer
  -> FastAPI router container
       -> authentication, request limits, metrics, correlation ID
       -> circuit breaker and ordered provider selection
       -> configured AI providers or development-only mock fallback
```

`POST /v1/chat/completions` requires `X-API-Key`. The process exposes liveness, readiness, and Prometheus metrics endpoints. Provider state, rate limiting, and circuit breakers are in-memory and therefore scoped to one application process.

For more than one worker or replica, use a shared state backend before treating circuit or rate-limit behavior as globally coordinated.
