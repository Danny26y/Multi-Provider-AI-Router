# Dashboard contracts and security

The Streamlit process is the router's client. Browsers receive rendered results,
not the router credential. Configure flat `DASHBOARD_ROUTER_URL`,
`DASHBOARD_API_KEY`, and optionally `DASHBOARD_REQUEST_TIMEOUT_SECONDS` values in
the environment or `.streamlit/secrets.toml` on the server. `ROUTER_API_KEY` is a
fallback when a separate dashboard key setting is omitted. Environment values
override secrets; `.env` is loaded without overriding an existing environment.
The repository already ignores `.env` and `.streamlit/`.

Only trusted server operators configure the target URL. Userinfo, query strings,
fragments, known provider API hosts, and URLs containing the configured key are
rejected. Remote routers require HTTPS; loopback HTTP is for local testing.
Redirects are not followed, so the custom authentication header is not forwarded
to another destination. The dashboard contains no direct provider integration.

## Existing endpoint use

| Endpoint | Dashboard use | Current router access |
| --- | --- | --- |
| `POST /v1/chat/completions` | User-triggered prompt; `X-API-Key` header | Authenticated |
| `GET /health` | Compatibility status (`online`) | Public |
| `GET /health/live` | Liveness (`online`) | Public |
| `GET /health/ready` | Configuration readiness (`ready` or HTTP 503) | Public |
| `GET /metrics` | Manual, filtered Prometheus snapshot | `X-Metrics-Key` when `METRICS_API_KEY` is configured; otherwise public |

The chat key is sent only to the authenticated chat endpoint. Checkpoint E adds
an optional separate `DASHBOARD_METRICS_API_KEY` (fallback `METRICS_API_KEY`) sent
only as `X-Metrics-Key` to `/metrics`; its value is also redacted from displays.
The metrics guard is opt-in to preserve existing clients; configure it for deployment.
Health checks are snapshots, not inference tests
or continuous monitoring. Missing health fields are shown as unknown. Readiness
does not prove that remote credentials or providers are currently working.

## Three distinct scopes of telemetry

1. Session observations: last 100 submissions, with observed outcomes, measured
   client round trips, reported provider/model, finish reason, usage flags, and
   fallback flags. Charts count only observed selections and measured values.
   They are not global provider-performance statistics. Missing fields stay null;
   unreported fallback flags are excluded from the known-flag denominator.
2. Response routing diagnostics: supplied by the router for a specific success or
   terminal failure. Only known outcomes/categories, safe provider identifiers,
   and reported boolean flags are displayed. Unknown fields and raw errors are
   discarded. An open-circuit skip is not interpreted as an HTTP attempt.
3. Server metrics: known `ai_router_*` counters, histogram sums/counts, and circuit
   gauge samples from `/metrics`. No process metrics, arbitrary metric families,
   or raw exposition text are displayed. Empty snapshots are handled explicitly.
   Counters are cumulative and a load-balanced snapshot is not a combined view
   across workers or replicas. Circuit gauges appear only if the server reports
   them; they are not a live provider-connectivity guarantee.

The response's `latency_seconds` measures the selected provider attempt. The
dashboard measures its HTTP round trip separately and never substitutes one for
the other. Token estimates and cost-estimation flags are shown as reported;
missing usage is not filled with zero. `finish_reason: length` preserves the
visible answer and warns of possible truncation. Local mock fallback is labeled
as non-generated output.

## Error and retention policy

HTTP failures and transport exceptions map to fixed, useful messages. Raw error
bodies, exception text, stack traces, headers, and connection URLs are not rendered
or logged by the dashboard client. The configured router key is redacted from
returned content and safe labels as a defense against accidental echoing.
Credentials are never widget values or cached client objects.

History omits prompts and answer text; the latest mapped response remains in
Streamlit session state so it survives reruns. The prompt widget also retains its
current input. Clear history or end the session to discard displayed responses.
The dashboard is not an authentication boundary: restrict access to its UI,
because its users act with the server-held router credential. Public metrics
access without a configured metrics key is a pre-existing contract and should be restricted at the network edge
where required. No new unauthenticated infrastructure details are exposed.

The dashboard uses finite HTTP phase timeouts and no retries. These are not a
hard wall-clock cap for a peer that continually trickles response bytes. The
router retains its own shared inference deadline.
