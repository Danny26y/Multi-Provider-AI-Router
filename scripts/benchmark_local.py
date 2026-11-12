"""Bounded single-process Locust run against local mocked provider adapters."""

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import socket
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path

import httpx

from scripts.demo import ROOT, environment, verify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("success", "fallback"), default="fallback")
    parser.add_argument("--users", type=int, choices=(1, 2, 5, 10), default=2)
    parser.add_argument("--seconds", type=int, choices=(10, 20, 30, 60), default=20)
    parser.add_argument("--output", type=Path, default=Path("benchmarks/results/local"))
    args = parser.parse_args()
    if importlib.util.find_spec("locust") is None:
        parser.error("Install the optional hash-locked benchmark dependencies first.")
    args.output.mkdir(parents=True, exist_ok=True)
    env = environment(args.scenario, 8000, 9000)
    env["LOCUST_ROUTER_API_KEY"] = env["ROUTER_API_KEY"]
    env["LOCUST_ROUTING_SUMMARY"] = str(args.output.resolve() / "routing.json")
    children = []
    try:
        for port in (8000, 9000):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        with (args.output / "services.log").open("w", encoding="utf-8") as log:
            for module, port in (("benchmarks.mock_upstream:app", 9000), ("main:app", 8000)):
                children.append(subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port),
                     "--no-access-log", "--log-level", "warning"], cwd=ROOT, env=env, stdout=log, stderr=log,
                ))
            with httpx.Client(base_url="http://127.0.0.1:8000", timeout=3, trust_env=False) as client:
                deadline = time.monotonic() + 20
                for port, path in ((9000, "/openapi.json"), (8000, "/health/live")):
                    while True:
                        if any(child.poll() is not None for child in children):
                            raise RuntimeError("A local service exited.")
                        try:
                            if client.get(f"http://127.0.0.1:{port}{path}").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Local readiness deadline expired.")
                        time.sleep(0.1)
                verify(client, env, args.scenario)  # Excluded from Locust statistics.
            metadata = {
                "scope": "native local mock HTTP; no live provider or deployment",
                "platform": platform.platform(), "python": platform.python_version(),
                "logical_cpus": os.cpu_count(), "resource_limits": "No CPU or memory isolation applied",
                "users": args.users, "spawn_per_second": 1, "requested_seconds": args.seconds,
                "wait_seconds": [0.1, 0.5], "workers": 1, "scenario": args.scenario,
                "client_rate_limit_per_minute": 10000, "router_concurrency": 20,
                "warmup": "One authenticated preflight completion before measurement; fresh server per run",
                "packages": {name: version(name) for name in ("locust", "fastapi", "httpx", "uvicorn")},
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "source_state": "Working tree; workload file hashes identify the measured harness",
                "sha256": {
                    name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                    for name in ("benchmarks/locustfile.py", "benchmarks/mock_upstream.py", "benchmarks/observations.py",
                                 "scripts/demo.py", "scripts/benchmark_local.py", "requirements-benchmark.txt")
                },
            }
            (args.output / "environment.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            with (args.output / "locust.log").open("w", encoding="utf-8") as locust_log:
                result = subprocess.run(
                    [sys.executable, "-m", "locust", "-f", "benchmarks/locustfile.py", "--host",
                     "http://127.0.0.1:8000", "--headless", "--users", str(args.users), "--spawn-rate", "1",
                     "--run-time", f"{args.seconds}s", "--stop-timeout", "5", "--csv",
                     str(args.output.resolve() / "run"), "--only-summary"],
                    cwd=ROOT, env=env, stdout=locust_log, stderr=locust_log, timeout=args.seconds + 45,
                )
            if result.returncode != 0:
                raise RuntimeError("Locust failed; inspect sanitized local artifacts.")
            print((args.output / "routing.json").read_text(encoding="utf-8"))
        return 0
    except (OSError, RuntimeError, AssertionError, ValueError, httpx.HTTPError, subprocess.TimeoutExpired):
        print("Local benchmark failed. Check ports, dependencies, and retained artifacts.", file=sys.stderr)
        return 1
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
