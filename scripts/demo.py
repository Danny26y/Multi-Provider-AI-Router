"""Opt-in, loopback-only demonstration using the real router and fake upstreams."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "success": ("success", "success"),
    "fallback": ("outage", "success"),
    "failure": ("outage", "outage"),
    "reasoning": ("reasoning", "success"),
    "truncated": ("truncated", "success"),
}


def environment(scenario: str, router_port: int, upstream_port: int) -> dict[str, str]:
    """Override local dotenv/provider settings; no configured remote route can run."""
    first, second = SCENARIOS[scenario]
    return {
        **os.environ,
        "APP_ENV": "testing", "ENABLE_MOCK_FALLBACK": "false",
        "ROUTER_API_KEY": secrets.token_urlsafe(32), "METRICS_API_KEY": secrets.token_urlsafe(32),
        "GROQ_API_KEY": "local-demo-placeholder", "NVIDIA_API_KEY": "local-demo-placeholder", "CEREBRAS_API_KEY": "",
        "GROQ_BASE_URL": f"http://127.0.0.1:{upstream_port}/{first}/v1",
        "NVIDIA_BASE_URL": f"http://127.0.0.1:{upstream_port}/{second}/v1",
        "PROVIDER_ORDER": '["groq_fast","nvidia"]', "NVIDIA_ENABLED": "true", "CEREBRAS_ENABLED": "false",
        "GROQ_FAST_MODEL": "demo-fast", "NVIDIA_DEFAULT_MODEL": "demo-nvidia",
        "NVIDIA_ALLOWED_MODELS": '["demo-nvidia"]', "PROVIDER_MODEL_ALIASES": '{}',
        "CLIENT_RATE_LIMIT_PER_MINUTE": "10000", "MAX_CONCURRENT_REQUESTS": "20",
        "DASHBOARD_ROUTER_URL": f"http://127.0.0.1:{router_port}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def verify(client: httpx.Client, env: dict[str, str], scenario: str) -> dict:
    payload = {"messages": [{"role": "user", "content": "Demonstrate safe routing."}]}
    assert client.post("/v1/chat/completions", json=payload).status_code == 401
    for path in ("/health", "/health/live", "/health/ready"):
        assert client.get(path).status_code == 200
    assert client.get("/metrics").status_code == 401
    response = client.post("/v1/chat/completions", json=payload, headers={"X-API-Key": env["ROUTER_API_KEY"]})
    data = response.json()
    assert "simulated-upstream-private-body" not in response.text
    assert all(env[key] not in response.text for key in ("ROUTER_API_KEY", "METRICS_API_KEY"))
    if scenario == "failure":
        assert response.status_code == 503
        assert data["error"]["code"] == "no_provider_available"
        diagnostics = data["error"]["routing_diagnostics"]
        assert all(item["category"] == "server_error" for item in diagnostics)
    else:
        assert response.status_code == 200
        expected = "nvidia" if scenario in {"fallback", "reasoning"} else "groq_fast"
        assert data["provider_used"] == expected
        assert data["choices"][0]["message"]["content"] == "mock response"
        assert data["fallback_used"] == (scenario in {"fallback", "reasoning"})
        assert data["choices"][0]["finish_reason"] == ("length" if scenario == "truncated" else "stop")
        diagnostics = data["routing_diagnostics"]
    metrics = client.get("/metrics", headers={"X-Metrics-Key": env["METRICS_API_KEY"]})
    assert metrics.status_code == 200 and "ai_router_http_requests_total" in metrics.text
    return {"scenario": scenario, "status": response.status_code, "verified": True, "routing_diagnostics": diagnostics}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, default="fallback")
    parser.add_argument("--router-port", type=int, default=8000)
    parser.add_argument("--upstream-port", type=int, default=9000)
    parser.add_argument("--dashboard", action="store_true", help="Also start Streamlit on loopback port 8501.")
    parser.add_argument("--verify-only", action="store_true", help="Verify then stop all child services.")
    args = parser.parse_args()
    ports = [args.router_port, args.upstream_port] + ([8501] if args.dashboard else [])
    if len(set(ports)) != len(ports) or any(not 1 <= port <= 65535 for port in ports):
        parser.error("Choose distinct ports between 1 and 65535.")
    env = environment(args.scenario, args.router_port, args.upstream_port)
    # Dashboard inherits generated demo credentials, overriding existing configuration.
    env["DASHBOARD_API_KEY"] = env["ROUTER_API_KEY"]
    env["DASHBOARD_METRICS_API_KEY"] = env["METRICS_API_KEY"]
    children = []
    try:
        for port in ports:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        for module, port in (("benchmarks.mock_upstream:app", args.upstream_port), ("main:app", args.router_port)):
            children.append(subprocess.Popen(
                [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port),
                 "--no-access-log", "--log-level", "warning"], cwd=ROOT, env=env,
            ))
        with httpx.Client(base_url=f"http://127.0.0.1:{args.router_port}", timeout=3, trust_env=False) as client:
            deadline = time.monotonic() + 20
            for port, path in ((args.upstream_port, "/openapi.json"), (args.router_port, "/health/live")):
                while True:
                    if any(child.poll() is not None for child in children):
                        raise RuntimeError("A demo child process exited.")
                    try:
                        if client.get(f"http://127.0.0.1:{port}{path}").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Local startup deadline exceeded.")
                    time.sleep(0.1)  # Poll actual readiness; not a fixed startup delay.
            print(json.dumps(verify(client, env, args.scenario), indent=2))
        if args.dashboard:
            children.append(subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", "dashboard.py", "--server.address", "127.0.0.1",
                 "--server.port", "8501", "--server.headless", "true", "--browser.gatherUsageStats", "false"],
                cwd=ROOT, env=env,
            ))
            print("Dashboard: http://127.0.0.1:8501 (local mock providers only)")
        if not args.verify_only:
            print("Demo is running. Submit dashboard prompts; Ctrl+C stops child services.")
            while all(child.poll() is None for child in children):
                time.sleep(0.5)
            raise RuntimeError("A demo child process exited.")
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, AssertionError, httpx.HTTPError, ValueError):
        print("Demo failed. Check local ports, dependencies, and scenario configuration.", file=sys.stderr)
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
