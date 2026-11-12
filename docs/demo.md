# Deterministic local demonstration

Use Python 3.12 with the hash-locked CI dependencies from the README. No provider
account, paid API, container, or public listener is required. Run from the
repository root; `$Python` refers to the isolated environment's interpreter.

## Verify each scenario

The legacy command `python test_failover.py` is retained as a guarded alias for
the fallback verification below. Importing/collecting it performs no network I/O.
It replaces the old unauthenticated client and outdated health assumptions.

```powershell
& $Python scripts/demo.py --scenario success --verify-only
& $Python scripts/demo.py --scenario fallback --verify-only
& $Python scripts/demo.py --scenario failure --verify-only
& $Python scripts/demo.py --scenario reasoning --verify-only
& $Python scripts/demo.py --scenario truncated --verify-only
```

Each invocation creates fresh router/upstream processes, checks startup readiness,
verifies the scenario, prints sanitized routing diagnostics, and stops its children.
Exit 0 means all assertions passed; a nonzero exit is a failed demonstration.
Default ports are 8000/9000. Use `--router-port 8100 --upstream-port 9100` if
occupied. Distinct ports are checked before startup. Services bind only to
`127.0.0.1`; do not port-forward this unauthenticated upstream simulator.

| Scenario | Local HTTP upstream behavior | Expected router behavior |
| --- | --- | --- |
| `success` | Groq-labelled route returns valid text | 200, `groq_fast`, no fallback |
| `fallback` | First route returns 503; NVIDIA-labelled route returns text | 200, `nvidia`, fallback true |
| `failure` | Both routes return 503 with a simulated private body | Sanitized 503; raw body absent |
| `reasoning` | First returns null content with `length`; second returns text | 200, NVIDIA fallback, `unusable_output` diagnostic |
| `truncated` | First returns visible text with `length` | 200, original finish reason retained |

All scenarios also assert that chat without a key returns 401, public health
endpoints return 200 for this eligible local configuration, metrics without a key
return 401, and metrics with the separate generated key return 200. Readiness
200 demonstrates eligible configuration, not a live provider check.

The demo overrides existing dotenv/provider URL, key, order, model, and mock
fallback settings. All eligible remote-style adapters point to the local simulator
and use placeholders. Its generated router/metrics secrets are passed only in
child environments and are not printed. The mock upstream never forwards requests.
These are real local HTTP calls through the router's adapter/validation path,
not calls to Groq or NVIDIA. The built-in `mock_fallback` is disabled here.

## Dashboard presentation

```powershell
& $Python scripts/demo.py --scenario fallback --dashboard
```

1. Open `http://127.0.0.1:8501`. The dashboard receives generated keys server-side.
2. Click **Check connection** and inspect the supported health statuses.
3. Enter any short prompt; leave explicit model and token override unset.
4. Click **Send through router**. Expect `mock response`, selected provider
   `nvidia`, returned model `demo-nvidia`, and fallback true.
5. Inspect diagnostics: the first request fails with `server_error`; the next
   route succeeds. After three failures the Groq circuit opens, so later prompts
   may show `circuit_open` skips instead of another HTTP attempt.
6. Click **Fetch server metrics**. Its snapshot is separate from session charts.
7. Restart with `--scenario truncated` or `--scenario failure` to demonstrate
   visible truncation warnings or sanitized failure presentation. Ctrl+C stops
   the demo's child processes; this Windows convenience shutdown is not the
   container graceful-shutdown acceptance test.

No explicit model should be entered for the fallback presentation: requesting
`demo-fast` correctly prevents substitution with `demo-nvidia`.
Session token counts are fixed simulator metadata, not measured model tokenization.

For an automated headless UI presentation check:

```powershell
& $Python -m pytest tests/test_dashboard_ui.py tests/test_demo_benchmark.py -v
```

AppTest submits through the dashboard code using a deterministic HTTP transport;
it does not prove a browser-to-live-router interaction. The separate local HTTP
demonstrations verify actual routing. See the final verification record for which
manual interactions were performed.

## Live provider checks are separate

Only explicitly invoking `python scripts/nvidia_smoke.py` with real credentials
or configuring real upstream URLs/keys performs live inference. This may consume
quota or incur provider charges. It is not needed for this demonstration, tests,
or CI and is not run as part of Checkpoint F.
