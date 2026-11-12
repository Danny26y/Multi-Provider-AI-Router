# Engineering case study: reliable text routing across AI providers

## Problem

A client integrating one model endpoint inherits its outages, quotas, response
quirks, and latency. Adding providers independently can multiply secrets,
error handling, and inconsistent retry behavior. This project puts those decisions
behind one secured chat API and makes each routing outcome inspectable.

The goal is a reviewable engineering system, not a claim of production scale,
model-quality improvement, or a deployed commercial service.

## Design decisions

The router extends a shared HTTPX asynchronous adapter rather than introducing
separate SDK stacks. One lifespan-managed connection pool serves Groq fast,
NVIDIA NIM, Groq reasoning, and optional Cerebras. Ordered eligibility keeps
the default path predictable. One monotonic deadline covers failover; each route
is attempted at most once. Explicit model requests narrow eligibility instead
of allowing an answer from a silently substituted model.

Multi-provider routing offers another eligible path when one endpoint fails,
but does not guarantee availability: upstreams can share failures, a requested
model may have no alternative, and every attempt consumes the shared budget.

## Reliability and NVIDIA reasoning output

HTTP success is checked separately from usable model output. Every choice must
contain assistant text, a valid finish reason, matching model identity (or a
configured directional alias), and internally consistent usage when supplied.
Missing optional usage is estimated and explicitly marked.

NVIDIA reasoning models can consume generation tokens internally and return
null visible content with `finish_reason: length`. Such output is classified as
unusable and may fail over; it is not displayed as an answer. Visible text with
`length` is retained and marked as possibly truncated. Internal reasoning fields
are not exposed as assistant text. Larger token budgets do not extend deadlines.

Circuit health is reserved for availability/reliability failures: transport,
timeouts, 429, 5xx, and malformed responses. Authentication, configuration,
model mismatch, and unusable output do not add health failures. A bounded
provider-scoped cooldown honors `Retry-After`; a single half-open probe controls
recovery. Cancellation and inconclusive probes release their slot. Success
received while a circuit remains open does not reset its health.

## Testing and operational engineering

Tests use deterministic HTTP transports, controlled timing, malformed payloads,
classification assertions, circuit transitions, deadline exhaustion, and explicit
model constraints. Streamlit AppTest verifies presentation and sanitized errors;
real loopback HTTP demos exercise the actual API and provider adapter. The
benchmark mock is validated by that same semantic-response path.

The verified Checkpoint E baseline was 238 passing tests on Windows and Linux,
97% reported coverage of `app`, `config`, and `main`, clean dependency audits,
and successful Linux container build/runtime probes. Coverage is not a guarantee
of correctness or a measure of external model quality. Checkpoint F adds harness
regressions; its exact final results are in [verification](verification.md).

API-key authentication fails closed for chat. Metrics have a separate optional
key and must be protected before exposure. Minimal health probes stay usable for
orchestration. Boundaries include request/body/queue limits, non-root containers,
root-owned code, sanitized errors, bounded metric labels, secret exclusion, and
reviewed dependency pins with generated transitive hashes. CI blocks every known
advisory and feed failures; it does not deploy or need provider credentials.

The dashboard sends all inference through the router and separates session-only
observations from response diagnostics and server Prometheus snapshots. A server
API key does not authenticate dashboard viewers; public hosting requires protected
HTTPS ingress. Local deployment readiness is distinct from production acceptance.

## Evidence, limitations, and next steps

- [Architecture and deployment boundaries](architecture.md)
- [Deterministic demonstration](demo.md)
- [Benchmark method and limits](../benchmarks/README.md)
- [Recorded measurements](benchmark-results.md)
- [Final verification](verification.md)

State and limits are process-local; there is no persistent/global coordination,
streaming, tool-call output, built-in HTTPS, or production deployment. Live
provider behavior and quotas need separately authorized checks. Local mock
throughput is not model speed or a production capacity estimate.

Future work should be driven by evidence: staging acceptance behind authenticated
ingress, OS-image scanning, shared limit/breaker semantics before replication,
explicit streaming/tool-call contracts, and workload-specific latency/cost studies.
These are proposals, not features implemented by this checkpoint.
