# Benchmark Harness

This optional Locust harness measures the router against a local mock upstream. It does not use provider credentials or paid APIs.

Install benchmark-only dependencies:

```powershell
python -m pip install -r requirements-benchmark.txt
```

Start the mock upstream:

```powershell
python -m uvicorn benchmarks.mock_upstream:app --host 127.0.0.1 --port 9000
```

In another terminal, configure and start the router so `groq_fast` targets the mock upstream:

```powershell
$env:ROUTER_API_KEY = "benchmark-local-key"
$env:APP_ENV = "testing"
$env:ENABLE_MOCK_FALLBACK = "false"
$env:GROQ_API_KEY = "benchmark-placeholder"
$env:GROQ_BASE_URL = "http://127.0.0.1:9000/v1"
$env:PROVIDER_ORDER = '["groq_fast"]'
python main.py
```

Run Locust and write CSV output:

```powershell
$env:LOCUST_ROUTER_API_KEY = "benchmark-local-key"
locust -f benchmarks/locustfile.py --host http://127.0.0.1:8000 --headless -u 20 -r 5 -t 60s --csv benchmarks/results/run
```

Record the Locust output's request count, error rate, p50, p95, and throughput beside the command parameters and host hardware. Do not compare runs with different worker counts, limits, or machine resources.
