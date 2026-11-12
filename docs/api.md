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

The response includes `provider_used`, `fallback_used`, selected-provider-attempt
`latency_seconds`, token usage, and sanitized routing diagnostics. That latency
does not include prior attempts or API queue time; measure client wall time or
the routing histogram for a broader duration. Responses omit provider credentials
and raw upstream exception content.

## Completion validation and diagnostics

A remote completion is selected only after validating its JSON object, nonempty
choices, assistant roles, text content, finish reasons, model identity, and any
supplied token usage. Absent or null usage is allowed and produces estimated
counts. Supplied usage must contain nonnegative integer prompt, completion, and
total counts; the total must equal prompt plus completion. Supplied token-detail
counters must also be valid and fit within their corresponding count.

For this text-only API, `stop` and `length` are usable finish reasons when the
assistant content contains non-whitespace text. A visible answer ending with
`length` may be truncated. Null, empty, whitespace-only, reasoning-only,
tool-call, and filtered outputs are classified as `unusable_output` and fail over.
Malformed structures or invalid/missing finish reasons are `invalid_response`.
Every returned choice is validated. Separate internal reasoning and optional
provider message metadata are omitted from the public assistant message.

The returned model must exactly match the model sent to that provider, unless an
alias is configured in `PROVIDER_MODEL_ALIASES`. Aliases are directional and scoped
to a provider and requested model. They permit a returned identity only; they do
not expand request allowlists or change the outbound model. For example:

```dotenv
PROVIDER_MODEL_ALIASES={"groq_fast":{"llama-3.1-8b-instant":["explicitly-approved-version"]}}
```

Existing diagnostic fields (`provider`, `outcome`, `category`) are retained. Each
entry adds `attempted`, `selected`, `fallback_used`, and
`circuit_breaker_affected`. An open-circuit skip is not an HTTP attempt. Mock
selection is also not a remote attempt. `fallback_used` is true for selection or
attempts after an earlier eligible provider failed or was unavailable; skipping
an incompatible model does not itself count as fallback. The top-level success
flag now reports remote fallback as well as mock fallback.

## Terminal routing errors

When eligible providers are exhausted, the API retains HTTP 503 and its existing
`detail` string, with an additive `error` object:

```json
{
  "detail": "No AI provider is currently available.",
  "error": {
    "code": "no_provider_available",
    "message": "No AI provider is currently available.",
    "fallback_used": false,
    "routing_diagnostics": []
  }
}
```

`code` is `deadline_exceeded` when the shared routing deadline expires. Diagnostics
contain only provider identifiers, fixed outcome/category values, and booleans;
they contain no upstream error bodies, exception text, model output, or credentials.
An explicit model still prevents fallback to a different model or the local mock.
