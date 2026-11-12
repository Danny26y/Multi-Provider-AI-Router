# Recorded benchmark observations

## Native Windows local mocks — November 11, 2026

These are bounded engineering demonstrations, **not a production capacity test**.
The router, upstream simulator, and load generator shared a Windows 11 host
(build 26100), Python 3.12.8, four logical CPUs, with no enforced CPU/memory limits.
Other verification/Docker startup activity occurred on the host; load isolation
was not controlled. Each scenario is one sample, not a statistically significant
comparison. Differences between the rows do not establish a performance advantage.

| Scenario | Requested duration | Actual test window | Requests | RPS | p50 | p95 | Success / failure | Selected / fallback |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| Direct mock success | 20 s | 18.703 s | 115 | 6.149 | 12 ms | 46 ms | 100% / 0% | 115 Groq-labelled; 0 fallback |
| Mock fallback/circuit bypass | 20 s | 18.766 s | 122 | 6.501 | 12 ms | 41 ms | 100% / 0% | 122 NVIDIA-labelled; 122 fallback |

Both runs used two users, one user spawned per second, 0.1–0.5 second random
think time, one Locust 2.47.0 process, one router worker, fixed short text,
10,000 requests/minute local client limit, and 20 router concurrency slots.
There were no truncated completions, recorded request failures, or generator
exceptions. Startup and one authenticated preflight completion are excluded.

The fallback route first receives simulated HTTP 503. After three failures its
circuit opens; later requests skip it and select the second local adapter. One
failure occurred in preflight. Consequently the fallback row mixes actual HTTP
failover and circuit bypass; it does not measure two HTTP exchanges per request.
No inference provider was contacted, and reported usage is fixed simulator data.

RPS above is requests / elapsed test-start-to-test-stop time from final
`routing.json`. Locust CLI startup explains why a requested 20-second run has a
shorter measured window. Percentiles are Locust's rounded histogram estimates.
The periodic `run_stats.csv` can lag final request drain and uses a different
RPS denominator; it is retained as raw evidence, not used for the table's totals.
`routing.csv` and `routing.json` are emitted at the same final event and agree.

## Evidence and reproduction

Reviewed [evidence directory](../benchmarks/evidence/2026-11-11-windows/) contains
final JSON/CSV, periodic Locust CSV, environment metadata, exact workload file
hashes, the Windows benchmark hash lock, its clean audit report, and SHA256 sums.
The source commit in metadata is the Checkpoint E parent; uncommitted Checkpoint F
workload hashes identify the actual measured code. No commit is claimed for work
that had not yet been committed.

Follow [the full procedure](../benchmarks/README.md). To reuse the archived
Windows dependency set rather than resolve it again:

```powershell
py -3.12 -m venv .venv/benchmark-replay
$BenchmarkPython = "$PWD/.venv/benchmark-replay/Scripts/python.exe"
& $BenchmarkPython -m pip install --require-hashes -r benchmarks/evidence/2026-11-11-windows/benchmark.lock
& $BenchmarkPython -m scripts.benchmark_local --scenario success --users 2 --seconds 20 --output benchmarks/results/replay-success
& $BenchmarkPython -m scripts.benchmark_local --scenario fallback --users 2 --seconds 20 --output benchmarks/results/replay-fallback
```

Re-run a strict audit before reuse: an advisory-free result is a dated observation,
not a permanent property. Different hosts, scheduling, random think times, or
background load will produce different measurements. Archive fresh Linux locks
for Linux replay; this Windows lock is not a Linux compatibility claim.

## Other scopes

| Measurement scope | Status |
| --- | --- |
| Native Windows loopback mock benchmark | Recorded above |
| Resource-constrained Linux benchmark | Not performed; Linux correctness/container checks are separate |
| Live Groq/NVIDIA/Cerebras inference benchmark | Not performed |
| Deployed HTTPS infrastructure benchmark | Not performed; no deployment exists |

No provider speed, cost saving, production throughput, availability, or answer
quality is inferred from these observations.
