"""Streamlit engineering dashboard. Credentials and HTTP requests stay server-side."""

import os
from datetime import datetime, timezone

import streamlit as st
from dotenv import load_dotenv

from app.services.dashboard_client import DashboardClient, DashboardConfig, DashboardConfigurationError, session_summary


def reported(value, suffix=""):
    return "Not reported" if value is None else f"{value}{suffix}"


def show_result(result):
    if result["outcome"] == "failed":
        st.error(result["message"])
        if result.get("error_code"):
            st.caption(f"Routing outcome: {result['error_code']}")
    else:
        if result["outcome"] == "truncated":
            st.warning("Truncated response — the output token limit was reached. The answer may be incomplete.")
        elif result.get("provider") == "mock_fallback":
            st.warning("Local mock fallback selected — this is not generated model output.")
        else:
            st.success("Completion received")
        st.text_area("Assistant response", value=result["content"], height=210, disabled=True)
        provider, model = st.columns(2)
        provider.metric("Selected provider", reported(result.get("provider")))
        model.metric("Returned model", reported(result.get("model")))
        st.caption(f"Finish reason: {reported(result.get('finish_reason'))}")
        timing, routing = st.columns(2)
        timing.write(f"Dashboard round trip: {reported(result.get('round_trip_seconds'), ' s')}")
        timing.write(f"Selected-provider latency: {reported(result.get('provider_latency_seconds'), ' s')}")
        fallback = result.get("fallback_used")
        routing.write(f"Fallback used: {'Not reported' if fallback is None else 'Yes' if fallback else 'No'}")
        usage = result.get("usage", {})
        estimated = usage.get("token_usage_is_estimated")
        st.caption("Token usage: " + ("estimated" if estimated is True else "reported by upstream" if estimated is False else "estimation status not reported"))
        st.dataframe([{key: usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}])
        if usage.get("estimated_cost_usd") is not None:
            st.caption(f"Router-reported cost: ${usage['estimated_cost_usd']:.6f} USD; estimation flag: {reported(usage.get('cost_is_estimated'))}")
    with st.expander("Response-level routing diagnostics", expanded=result["outcome"] == "failed"):
        if result.get("diagnostics"):
            st.dataframe(result["diagnostics"])
        else:
            st.info("No routing diagnostics were reported for this request.")


def main():
    load_dotenv()
    st.set_page_config(page_title="AI Router | Operations", page_icon="↗", layout="wide")
    st.title("AI Router")
    st.caption("Prompt testing and operational visibility · all inference goes through the router")
    try:
        try:
            secrets = dict(st.secrets)
        except FileNotFoundError:
            secrets = {}
        config = DashboardConfig.from_sources(os.environ, secrets)
    except DashboardConfigurationError as error:
        st.error(str(error))
        st.info("Configure DASHBOARD_ROUTER_URL and DASHBOARD_API_KEY on the dashboard server, then reload.")
        return
    client = DashboardClient(config)
    st.session_state.setdefault("history", [])
    st.sidebar.header("Router connection")
    st.sidebar.caption("Target configured on the server. Keys and full connection URLs are never displayed.")
    if not config.api_key:
        st.sidebar.warning("Prompt authentication is not configured.")
    if st.sidebar.button("Check connection", key="check_connection"):
        with st.spinner("Checking router health…"):
            st.session_state["health"] = client.health()
            st.session_state["health_checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    health = st.session_state.get("health")
    if health:
        live = health["/health/live"]
        ready = health["/health/ready"]
        if live.get("status") == "online" and ready.get("status") == "ready":
            st.sidebar.success("Router online · ready")
        elif live.get("status") == "online":
            st.sidebar.warning("Router online · readiness not confirmed")
        else:
            st.sidebar.warning("Connection not confirmed")
        for path, result in health.items():
            st.sidebar.write(f"{path}: {result.get('status', 'unavailable')}")
            if result.get("message"):
                st.sidebar.caption(result["message"])
        st.sidebar.caption(f"Last checked (UTC): {st.session_state['health_checked']}")
    else:
        st.sidebar.info("Connection not checked")
    st.sidebar.caption("Readiness reports configuration, not a live inference test. Health endpoints do not expose provider circuits.")
    prompt_tab, history_tab, metrics_tab = st.tabs(["Prompt test", "Session observations", "Server metrics"])
    with prompt_tab:
        st.subheader("Test a routed completion")
        with st.form("prompt_test"):
            prompt = st.text_area("Prompt", height=140, max_chars=20_000, key="prompt")
            model = st.text_input("Explicit model (optional)", max_chars=128, help="Leave blank for normal provider fallback. Explicit models must be in the router allowlist.", key="model")
            left, right = st.columns(2)
            temperature = left.slider("Temperature", 0.0, 2.0, 0.7, 0.1)
            override = right.checkbox("Override output token budget", value=False)
            tokens = right.number_input("Max output tokens", min_value=1, value=1024, step=256, help="The router validates and may cap this budget. Reasoning consumes output tokens too.")
            submitted = st.form_submit_button("Send through router", disabled=not bool(config.api_key))
        if submitted:
            with st.spinner("Waiting for the router…"):
                result = client.prompt(prompt, model=model.strip(), temperature=temperature, max_tokens=int(tokens) if override else None)
            result["observed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            st.session_state["last_result"] = result
            st.session_state["history"] = [*st.session_state["history"], {
                key: result.get(key) for key in (
                    "observed_at", "outcome", "provider", "model", "finish_reason", "round_trip_seconds",
                    "provider_latency_seconds", "fallback_used", "usage",
                )
            }][-100:]
        if st.session_state.get("last_result"):
            st.divider()
            show_result(st.session_state["last_result"])
        else:
            st.info("Send a prompt to inspect the returned answer, provider, and routing decisions.")
    with history_tab:
        st.subheader("This dashboard session")
        st.caption("Last 100 submissions in this browser session. These are observations, not global provider performance. History excludes prompts and answers.")
        if st.button("Clear session history"):
            st.session_state["history"] = []
            st.session_state.pop("last_result", None)
            st.rerun()
        history = st.session_state["history"]
        summary = session_summary(history)
        if not history:
            st.info("No requests observed in this session yet.")
        else:
            a, b, c, d = st.columns(4)
            a.metric("Session submissions", summary["requests"])
            b.metric("Completed", summary["completed"])
            c.metric("Truncated", summary["truncated"])
            d.metric("Failed", summary["failed"])
            st.caption(f"Reported fallback selections: {summary['reported_fallbacks']} / {summary['fallback_flags_reported']} responses with a fallback flag. Mean observed round trip: {reported(round(summary['mean_round_trip_seconds'], 3) if summary['mean_round_trip_seconds'] is not None else None, ' s')}")
            st.dataframe([{**{key: value for key, value in row.items() if key != "usage"}, **(row.get("usage") or {})} for row in history])
            chart_left, chart_right = st.columns(2)
            elapsed = {index + 1: row["round_trip_seconds"] for index, row in enumerate(history) if row.get("round_trip_seconds") is not None}
            if elapsed:
                chart_left.caption("Observed round trip (seconds) by session submission")
                chart_left.line_chart({"Round trip (s)": elapsed})
            if summary["provider_selections"]:
                chart_right.caption("Reported provider selections in this session")
                chart_right.bar_chart({"Selections": summary["provider_selections"]})
    with metrics_tab:
        st.subheader("Router Prometheus snapshot")
        st.caption("Server-reported cumulative samples from /metrics, separate from session history. A snapshot is not a rate or a cross-replica aggregate. Only known router metric families are shown.")
        if st.button("Fetch server metrics"):
            with st.spinner("Fetching router metrics…"):
                st.session_state["metrics"] = client.metrics()
                st.session_state["metrics_checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result = st.session_state.get("metrics")
        if result:
            st.caption(f"Snapshot (UTC): {st.session_state['metrics_checked']}")
            if result["outcome"] == "failed":
                st.error(result["message"])
            elif result.get("metrics"):
                st.dataframe(result["metrics"])
                st.caption("Circuit gauge, when reported: 0 = healthy, 1 = degraded, 2 = unhealthy. Samples are not a live connectivity guarantee.")
            else:
                st.info("No supported router metric samples were reported.")
        else:
            st.info("Fetch a snapshot to view actual server metrics.")


if __name__ == "__main__":
    main()
