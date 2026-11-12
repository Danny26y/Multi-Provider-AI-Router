# Architecture

These diagrams describe implemented behavior. HTTPS ingress in the final diagram
is a deployment requirement, not infrastructure provisioned by this repository.

## Request path

```mermaid
flowchart LR
    Client[API client] -->|X-API-Key| API[Secured FastAPI chat endpoint]
    API --> Limits[Validation, body, rate and concurrency limits]
    Limits --> Router[Ordered routing and shared deadline]
    Router --> Breaker[Per-route circuit admission]
    Breaker --> HTTP[Shared HTTPX async adapter]
    HTTP --> Groq[Groq fast or reasoning]
    HTTP --> NVIDIA[NVIDIA NIM]
    HTTP --> Cerebras[Optional Cerebras]
    Groq --> Validate[Semantic response validation]
    NVIDIA --> Validate
    Cerebras --> Validate
    Validate -->|Usable completion| Response[Content, usage, sanitized diagnostics]
    Response --> Client
    Router -->|Enabled only in development or testing| Mock[Fixed local mock fallback]
    Mock --> Response
```

`POST /v1/chat/completions` requires `X-API-Key`. The process exposes liveness, readiness, and Prometheus metrics endpoints. Provider state, rate limiting, and circuit breakers are in-memory and therefore scoped to one application process.

Provider-specific configuration shares one HTTP implementation; NVIDIA is not a
separate SDK path. `main.py` owns client startup/shutdown.
`app/services/provider_client.py` implements eligibility and bounded HTTP exchange.
`response_validation.py` checks every choice. The optional mock route makes no
HTTP requests and cannot satisfy an explicit model request.

## Selection and recovery

```mermaid
flowchart TD
    Start[Start one routing deadline] --> Next{Another configured route?}
    Next -->|No| Error[Sanitized terminal HTTP 503]
    Next -->|Yes| Budget{Time remains?}
    Budget -->|No| Deadline[Deadline exceeded HTTP 503]
    Budget -->|Yes| Eligible{Enabled, allowed model, credentials?}
    Eligible -->|No| Next
    Eligible -->|Yes| Circuit{Circuit admission?}
    Circuit -->|Open or probe occupied| Next
    Circuit -->|Healthy or single recovery probe| Attempt[Attempt once within remaining deadline]
    Attempt --> Valid{Valid usable text and model identity?}
    Valid -->|Yes, before deadline| Success[Record success and select response]
    Valid -->|No| Classify[Classify sanitized failure]
    Classify --> Health{Availability or reliability failure?}
    Health -->|Yes| Failure[Increment failures; 429 opens immediately]
    Health -->|No| Neutral[Preserve count; release inconclusive probe]
    Failure --> Next
    Neutral --> Next
```

Default remote sequence: Groq fast → NVIDIA → Groq reasoning, then optional
Cerebras when enabled. An explicit model narrows eligibility without substitution.
Every route is visited once; there are no unbounded retries.
The routing timer starts after API admission; body reads and concurrency queue
waits have their own separate bounds. It is not an end-to-end HTTP-client SLA.

Breaker states are `healthy`, `unhealthy` (open), and `degraded` (half-open).
Three health failures, or immediately a 429, make the route unavailable.
`Retry-After` seconds or HTTP-date is capped at 300 seconds by default; cooldown
is at least the normal 30 seconds. Only that route is affected. One probe is
admitted after cooldown: success closes it; failure reopens it; inconclusive or
cancelled probes return to open for another recovery interval. See the
[classification table](operations.md#failure-classification-and-circuit-health).

Each atomic `CircuitBreaker.acquire` returns a single-use admission token tied
to the breaker instance, provider, and current generation. Every state transition
invalidates earlier admissions. Only the active probe's token can close, reopen,
or release that probe; late success, failure, cancellation, and neutral cleanup
from older requests cannot change its state or cooldown. Healthy requests in the
same generation can still complete concurrently and update failure counts.

For internal callers, `is_available` remains a boolean query and does not claim a
probe. Sending requires `acquire`; `record_success`, `record_failure`, and
`record_neutral` require its token through the `admission` keyword to accept an
update. Missing, consumed, foreign, or stale tokens are ignored. The provider
client releases its token in `finally`, including errors immediately after
admission. HTTP request/response contracts and failure classifications are unchanged.

## Dashboard, health, and telemetry

```mermaid
flowchart LR
    User[Authorized dashboard viewer] --> Dashboard[Streamlit server]
    Dashboard -->|X-API-Key: prompt| Chat[Router chat endpoint]
    Chat -->|Content and response diagnostics| Session[Dashboard session observations]
    Session --> Dashboard
    Dashboard -->|Unauthenticated probes| Health[health, health/live, health/ready]
    Dashboard -->|X-Metrics-Key when configured| Metrics[Prometheus endpoint]
    Monitor[Private Prometheus scraper] -->|X-Metrics-Key when configured| Metrics
    Router[Request and provider events] --> Registry[Per-process metrics registry]
    Registry --> Metrics
    Metrics -->|Snapshot only| Dashboard
```

Session charts describe only that browser session. Response `latency_seconds`
measures the selected attempt; client wall time and routing histograms include
other work/failover. Metrics count terminal selections once and attempts
separately. Do not add total tokens to prompt/completion counts again.
No circuit information is inferred from health endpoints. Readiness checks
configuration, not connectivity. Registry and breaker state are per process.

## Deployment trust boundaries

```mermaid
flowchart LR
    subgraph Public[Untrusted network]
        Browser[Browser or API client]
    end
    subgraph Edge[Required deployment boundary]
        TLS[HTTPS termination and ingress limits]
        ViewerAuth[Dashboard viewer authentication]
    end
    subgraph Private[Private application network]
        API[Router: chat API-key authentication]
        Dash[Streamlit: server-held router key]
        Probes[Orchestrator probes]
        Scraper[Private metrics scraper]
    end
    subgraph External[External provider boundary]
        Providers[Configured HTTPS providers]
    end
    Browser --> TLS
    TLS -->|Chat traffic| API
    TLS --> ViewerAuth
    ViewerAuth --> Dash
    Dash -->|X-API-Key| API
    Probes -->|Minimal health contracts| API
    Scraper -->|Protected metrics| API
    API -->|Server-held provider credentials| Providers
```

The image runs non-root with root-owned code. Keys are server-side configuration,
not frontend assets. Metrics remain public when their optional key is unset:
protection is a required deployment choice. Ingress must also limit unauthenticated
traffic. Remote HTTP uses HTTPS without redirects or implicit environment proxies;
loopback HTTP is allowed for local demos. A shared chat key is not individual
identity. No public Streamlit deployment is included. For multiple workers or
replicas, design shared state before treating limits or breakers as global.
