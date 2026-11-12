import csv
import json
import os
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from locust import HttpUser, between, events, task

from benchmarks.observations import completion_observation

observations = Counter()
started = 0.0


@events.test_start.add_listener
def start(environment, **kwargs):
    global started
    if urlsplit(environment.host or "").hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("This deterministic harness requires a loopback router URL.")
    observations.clear()
    started = time.monotonic()


@events.test_stop.add_listener
def stop(environment, **kwargs):
    """Single-process run only: these are client observations, not global metrics."""
    stats = environment.stats.total
    elapsed = max(time.monotonic() - started, 0.001)
    report = {
        "scope": "local mock, single Locust process", "elapsed_seconds": elapsed,
        "requests": stats.num_requests, "failures": stats.num_failures,
        "requests_per_second": stats.num_requests / elapsed,
        "p50_ms": stats.get_response_time_percentile(0.5),
        "p95_ms": stats.get_response_time_percentile(0.95),
        "success_rate": (stats.num_requests - stats.num_failures) / stats.num_requests if stats.num_requests else None,
        "failure_rate": stats.num_failures / stats.num_requests if stats.num_requests else None,
        "observations": dict(observations),
    }
    output = os.environ.get("LOCUST_ROUTING_SUMMARY")
    if output:
        Path(output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        # Locust's periodic CSV snapshot can precede final request drain. Persist
        # final scalar statistics at the same event as the routing counters.
        with Path(output).with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
            scalars = {key: value for key, value in report.items() if key != "observations"}
            writer = csv.DictWriter(stream, fieldnames=list(scalars))
            writer.writeheader()
            writer.writerow(scalars)


class RouterUser(HttpUser):
    wait_time = between(0.1, 0.5)

    def on_start(self):
        self.client.trust_env = False
        if urlsplit(self.environment.host or "").hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("Use this harness only against a loopback mock router.")
        api_key = os.environ.get("LOCUST_ROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("Set LOCUST_ROUTER_API_KEY before starting Locust.")
        self.headers = {"X-API-Key": api_key}

    @task
    def chat_completion(self):
        with self.client.post(
            "/v1/chat/completions",
            headers=self.headers,
            json={"messages": [{"role": "user", "content": "Return a concise mock response."}]},
            name="/v1/chat/completions",
            catch_response=True,
            timeout=25,
            allow_redirects=False,
        ) as response:
            try:
                observed = completion_observation(response.json()) if response.status_code == 200 else None
            except ValueError:
                observed = None
            if observed is None:
                response.failure("Router request failed or returned unusable output.")
                observations["failed"] += 1
            else:
                observations[f"selected:{observed['provider']}"] += 1
                observations["fallback"] += int(observed["fallback"])
                observations["truncated"] += int(observed["truncated"])
