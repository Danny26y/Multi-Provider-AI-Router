# API Examples

## Liveness

```bash
curl https://router.example.com/health/live
```

## Chat completion

```bash
curl -X POST https://router.example.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ROUTER_API_KEY" \
  -H "X-Request-ID: example-request-id" \
  -d '{"messages":[{"role":"user","content":"Explain circuit breakers."}]}'
```

The response includes `provider_used`, `fallback_used`, request latency, token usage, and non-sensitive routing diagnostics. It never includes provider credentials or raw upstream exception content.
