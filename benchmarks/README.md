# Reproducible local benchmark

This optional Locust harness measures router overhead against **local mock HTTP
upstreams**. It does not measure Groq, NVIDIA, Cerebras, generated-answer quality,
or deployed infrastructure. Never replace local upstream URLs with real provider
URLs during this procedure.

## Isolated, hash-locked setup

Use the same Python 3.12 and uv 0.13.4 as the application. From the repository root,
with `$Python` pointing to the verification/development interpreter:

```powershell
& $Python scripts/lock_dependencies.py --platform windows --benchmark
py -3.12 -m venv .venv/benchmark
$BenchmarkPython = "$PWD/.venv/benchmark/Scripts/python.exe"
& $BenchmarkPython -m pip install --require-hashes -r .locks/benchmark.txt
& $BenchmarkPython -m pip check
& $Python -m pip_audit -r .locks/benchmark.txt --no-deps --disable-pip --strict
```

The pinned optional input is `requirements-benchmark.txt` (Locust 2.47.0).
`--benchmark` generates runtime, CI, and benchmark locks using the unchanged
November 8, 2026 cutoff. Benchmark resolution is constrained by the CI lock so
it cannot silently change application versions. The audit tool comes from the
CI environment; it need not be installed in the isolated load generator.
All known advisories and feed errors block acceptance. Do not install unlocked
dependencies or ignore findings to obtain a benchmark result.

Linux uses `--platform linux --benchmark` and `.venv/benchmark/bin/python`.
Windows and Linux outputs use the same filenames; archive each set before
switching targets. Locust is not required by the API image or normal tests.

## Bounded, automated run

Ensure local ports 8000 and 9000 are free. The runner starts fresh loopback-only
mock/router processes, generates ephemeral keys, performs one excluded preflight
completion, runs one Locust process, and stops its child services.

```powershell
& $BenchmarkPython -m scripts.benchmark_local --scenario success --users 2 --seconds 20 --output benchmarks/results/success
& $BenchmarkPython -m scripts.benchmark_local --scenario fallback --users 2 --seconds 20 --output benchmarks/results/fallback
```

Supported users: 1, 2, 5, or 10. Supported durations: 10, 20, 30, or 60 seconds.
These limits bound local load. Native processes share the host; **no CPU/memory
isolation is applied**, and results must say so. Do not imply a constrained
container benchmark from a native Windows run.

Workload: fixed short text prompt, no explicit model, one user spawned per second,
random 0.1–0.5 second think time, finite 25-second client timeout, redirects
disabled, one Uvicorn worker. This is closed-loop load, not a capacity/saturation
test. Proxy inheritance is disabled in the Locust HTTP session as well as the
router adapter so loopback credentials are not sent through environment proxies.
Startup, dependency imports, and the preflight request are outside the
measurement window; ramp-up and final request drain are included.

The runner overrides eligible upstreams to loopback, selects only Groq fast and
NVIDIA-labelled mock adapters, disables the built-in mock fallback, and raises
the local client limit to 10,000/minute so the normal 60/minute limit does not
dominate this small overhead experiment. Router concurrency remains 20.
Production defaults are unchanged.

## What the fallback scenario measures

The first adapter receives 503 responses. After three health failures it opens
its circuit. The second adapter returns validated mock text using the requested
model identity. One first-route failure occurs during preflight. Therefore early
measured requests include an HTTP failure and later ones usually include an
open-circuit skip. This is a mixed failover/circuit-bypass experiment, **not**
the cost of two HTTP attempts on every request. Inspect before/after server metrics
if attempt counts are needed; client fallback counts are terminal observations.

The repaired mock includes assistant role, requested model, finish reason, and
consistent fixed usage. Locust treats malformed/unusable HTTP 200 JSON as failure.
Visible `length` responses are usable but counted separately as truncated.
This harness does not assess semantic accuracy of generated answers.

## Artifacts and interpretation

Each output directory contains:

| File | Meaning |
| --- | --- |
| `run_stats.csv` | Locust's periodically written statistics; can precede final request drain |
| `run_stats_history.csv` | Periodic Locust time-window observations |
| `run_failures.csv`, `run_exceptions.csv` | Fixed/sanitized failure reasons and generator exceptions |
| `routing.json` | Client-observed selections, fallback/truncation counts, rates, latency percentiles |
| `routing.csv` | Final scalar statistics from the same test-stop event as `routing.json` |
| `environment.json` | Python/package versions, OS/CPU count, limits, workload, source commit and file hashes |
| `services.log`, `locust.log` | Local process logs for diagnosis; excluded from Git by default |

Use `routing.json` / `routing.csv` for final totals; the periodic Locust CSV can
lag by its final write interval. The CLI's requested duration and actual test
window can also differ because CLI setup occurs before the test-start event.
Record both. `routing.json` measures elapsed time from Locust test-start to test-stop.
Its RPS is total requests divided by that interval; Locust CSV RPS can differ
slightly because its request timestamp denominator differs. Report which one
you use. Percentiles are Locust histogram estimates in milliseconds, not exact
order statistics; include failures in the failure rate even if their latency is
not comparable to a usable completion. Success rate is (requests − failures) /
requests. The failure rate is its complement. No observations means unknown
rates/percentiles, not proof of reliability.

These counters support **one local Locust process**. Distributed/multi-process
aggregation is not implemented. Provider labels describe selected router adapters,
not proof that a remote provider was contacted. Response-level selected-attempt
latency is different from the client-observed request latency reported here.

Retain full raw results locally under ignored `benchmarks/results/`. Commit only
reviewed, sanitized evidence (CSV/JSON plus environment/source hashes) under
`benchmarks/evidence/`; never retain secrets or prompt histories.

## Manual equivalent and other measurement scopes

The existing modules can still be run manually:

```powershell
& $BenchmarkPython -m uvicorn benchmarks.mock_upstream:app --host 127.0.0.1 --port 9000
```

In another shell, configure the router with placeholder provider credentials,
`APP_ENV=testing`, `ENABLE_MOCK_FALLBACK=false`, and explicit loopback URLs.
`GROQ_BASE_URL=http://127.0.0.1:9000/outage/v1` and
`NVIDIA_BASE_URL=http://127.0.0.1:9000/success/v1` simulate fallback. Use
`PROVIDER_ORDER=["groq_fast","nvidia"]`, enable NVIDIA, and keep its default model
in its allowlist. Supply fresh router/metrics secrets through the environment;
start `python main.py`. Then set `LOCUST_ROUTER_API_KEY` in the load-generator
shell and invoke:

```powershell
$env:LOCUST_ROUTING_SUMMARY = "$PWD/benchmarks/results/manual-routing.json"
New-Item -ItemType Directory -Force benchmarks/results | Out-Null
& $BenchmarkPython -m locust -f benchmarks/locustfile.py --host http://127.0.0.1:8000 --headless --users 2 --spawn-rate 1 --run-time 20s --stop-timeout 5 --csv benchmarks/results/manual
```

The automatic runner is preferred because it owns configuration, readiness, keys,
and cleanup. Do not run the manual example against the server's default real URLs.

Live-provider studies require separate authorization, real credentials, quotas,
pricing, model/token parameters, and cost caps; this runner intentionally offers
no live-provider mode. Deployed-infrastructure studies additionally require an
approved target, HTTPS/network topology, ingress/replica/CPU/memory limits, and
load-generator location. None were performed by this checkpoint. Report them
in separate tables instead of extrapolating local mocks. See
[recorded results](../docs/benchmark-results.md).
