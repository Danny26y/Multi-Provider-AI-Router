"""Optional UI tests: install requirements-dashboard.txt to execute these in CI."""

from importlib import import_module
from pathlib import Path

import httpx
import pytest

from app.services.dashboard_client import DashboardClient

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
KEY = "dummy-ui-router-secret"


@pytest.fixture(scope="module")
def rendering_dependencies():
    """Initialize lazy rendering libraries outside the per-interaction timeout.

    AppTest.run already waits for script completion. Cold dataframe/chart imports
    belong to fixture setup, not the functional interaction's 15-second budget.
    Missing/broken dependencies still fail setup; no sleeps or retries hide errors.
    """
    for module in ("pandas", "pyarrow", "altair"):
        import_module(module)


@pytest.fixture
def dashboard(monkeypatch, rendering_dependencies):
    monkeypatch.setenv("DASHBOARD_ROUTER_URL", "https://router.test")
    monkeypatch.setenv("DASHBOARD_API_KEY", KEY)
    monkeypatch.setattr("dotenv.load_dotenv", lambda: None)
    status = {"code": 200, "finish": "stop"}
    requests = []

    def handler(req):
        requests.append(req)
        if req.url.path == "/metrics":
            return httpx.Response(200, text='ai_router_circuit_state{provider="nvidia"} 0\n')
        if req.url.path.startswith("/health"):
            return httpx.Response(200, json={"status": "ready" if req.url.path.endswith("ready") else "online"})
        assert req.headers["x-api-key"] == KEY
        if status["code"] != 200:
            return httpx.Response(status["code"], text=f"raw-provider-body {KEY}")
        return httpx.Response(200, json={
            "provider_used": "nvidia", "model": "nvidia/demo-model", "latency_seconds": 0.125,
            "fallback_used": True,
            "choices": [{"message": {"role": "assistant", "content": "The routed answer"}, "finish_reason": status["finish"]}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "token_usage_is_estimated": False},
            "routing_diagnostics": [{"provider": "nvidia", "outcome": "success", "selected": True}],
        })

    original_init = DashboardClient.__init__

    def init(self, config):
        original_init(self, config, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(DashboardClient, "__init__", init)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "dashboard.py"), default_timeout=15)
    return app, status, requests


def click(app, label):
    next(button for button in app.button if button.label == label).click().run()
    assert not app.exception


def test_dashboard_empty_history_and_connection_indicator(dashboard):
    app, _, requests = dashboard
    app.run()
    assert not app.exception
    assert not requests
    assert any("No requests observed" in entry.value for entry in app.info)
    click(app, "Check connection")
    assert any("Router online" in entry.value for entry in app.success)
    assert len(requests) == 3


def test_prompt_submission_renders_answer_and_observed_session_data(dashboard):
    app, _, requests = dashboard
    app.run()
    app.text_area(key="prompt").input("Hello")
    click(app, "Send through router")
    assert len(requests) == 1 and requests[0].url.host == "router.test"
    assert any(entry.value == "The routed answer" for entry in app.text_area)
    values = {entry.label: entry.value for entry in app.metric}
    assert values["Selected provider"] == "nvidia"
    assert values["Returned model"] == "nvidia/demo-model"
    assert values["Session submissions"] == "1"
    history = app.session_state["history"]
    assert history[0]["usage"]["total_tokens"] == 3
    assert "Hello" not in str(history) and KEY not in str(history)
    click(app, "Clear session history")
    assert app.session_state["history"] == []


def test_ui_auth_errors_are_sanitized(dashboard):
    app, status, _ = dashboard
    status["code"] = 401
    app.run()
    app.text_area(key="prompt").input("Hello")
    click(app, "Send through router")
    assert any("authentication failed" in entry.value for entry in app.error)
    assert KEY not in str([entry.value for entry in app.error])
    assert not app.exception
    assert app.session_state["history"][0]["outcome"] == "failed"


def test_ui_truncation_is_distinct_from_complete_success(dashboard):
    app, status, _ = dashboard
    status["finish"] = "length"
    app.run()
    app.text_area(key="prompt").input("Hello")
    click(app, "Send through router")
    assert any("Truncated response" in entry.value for entry in app.warning)
    assert not any("Completion received" in entry.value for entry in app.success)
    assert app.session_state["history"][0]["finish_reason"] == "length"


def test_ui_metrics_are_separate_from_session_observations(dashboard):
    app, _, requests = dashboard
    app.run()
    click(app, "Fetch server metrics")
    assert len(requests) == 1 and requests[0].url.path == "/metrics"
    assert app.session_state["history"] == []
    assert app.session_state["metrics"]["metrics"][0]["provider"] == "nvidia"
