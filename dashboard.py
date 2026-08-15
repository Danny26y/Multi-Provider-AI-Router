
import time
import requests
import streamlit as st
import plotly.express as px
import pandas as pd

# Router API Endpoints
PROXY_URL = "http://localhost:8000/v1/chat/completions"
HEALTH_URL = "http://localhost:8000/health"

st.set_page_config(
    page_title="AI Proxy Gateway Control Center",
    page_icon="⚡",
    layout="wide"
)

st.title("Enterprise AI Multi-Provider Proxy Gateway")
st.caption("Real-Time Circuit Breaker Status, Latency Telemetry, and Failover Management")

# Initialize session state for latency & cost history
if "history" not in st.session_state:
    st.session_state.history = []

# --- Sidebar: Operational Controls ---
st.sidebar.header("Gateway Controls")


def fetch_health():
    try:
        res = requests.get(HEALTH_URL, timeout=2.0)
        if res.status_code == 200:
            return res.json().get("circuit_breaker_states", {})
    except Exception:
        st.sidebar.error("Proxy Server Offline! Run 'python main.py'")
    return {}


states = fetch_health()

if st.sidebar.button("Refresh Provider Status"):
    st.rerun()

# --- Section 1: Provider Health Badges ---
st.subheader("Live Provider Circuit Breaker Health")
if states:
    cols = st.columns(len(states))
    for idx, (provider, data) in enumerate(states.items()):
        status = data.get("status", "unknown").upper()
        failures = data.get("failures", 0)

        with cols[idx]:
            if status == "HEALTHY":
                st.success(f"**{provider}**\n\nSTATUS: {status}\nFailures: {failures}")
            elif status == "DEGRADED":
                st.warning(f"**{provider}**\n\nSTATUS: {status}\nFailures: {failures}")
            else:
                st.error(f"**{provider}**\n\nSTATUS: {status}\nFailures: {failures}")
else:
    st.info("Start `main.py` to view active provider health status.")

st.divider()

# --- Section 2: Interactive Prompt Execution & Real-Time Test ---
col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("Live Prompt Test")
    test_prompt = st.text_area(
        "Input Test Prompt:",
        value="Explain the importance of multi-cloud AI infrastructure in 2 concise sentences.",
        height=100
    )

    if st.button("Send Payload Through Proxy"):
        payload = {"messages": [{"role": "user", "content": test_prompt}]}

        t0 = time.time()
        try:
            res = requests.post(PROXY_URL, json=payload, timeout=15)
            t1 = time.time()
            latency = round(t1 - t0, 3)

            # Inside dashboard.py -> if st.button("🚀 Send Payload Through Proxy"):

            if res.status_code == 200:
                data = res.json()
                provider = data.get("provider_used", "unknown")
                response_text = data["choices"][0]["message"]["content"]

                # 1. Extract the usage dictionary returned by FastAPI
                usage = data.get("usage") or {}
                tokens = usage.get("total_tokens", 0)
                cost = usage.get("estimated_cost_usd", 0.0)

                # 2. Extract latency returned by the API (or fallback to wall-clock time)
                latency = data.get("latency_seconds", round(t1 - t0, 3))

                # 3. Append to Streamlit session history
                st.session_state.history.append({
                    "Timestamp": time.strftime("%H:%M:%S"),
                    "Provider": provider,
                    "Latency (s)": latency,
                    "Total Tokens": tokens,
                    "Est. Cost ($)": f"{cost:.6f}"  # Format nicely for UI display
                })

                st.success(
                    f"**Fulfilled By:** `{provider}` in **{latency}s** | **Tokens:** `{tokens}` | **Cost:** `${cost:.6f}`")
                st.text_area("Response Output:", value=response_text, height=120)
            else:
                st.error(f"Error {res.status_code}: {res.text}")
        except Exception as e:
            st.error(f"Request failed: {e}")

with col_right:
    st.subheader(" Live Telemetry Logs")
    if st.session_state.history:
        df = pd.DataFrame(st.session_state.history)
        st.dataframe(df, use_container_width=True)

        # Summary metrics
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Requests", len(df))
        m2.metric("Avg Latency", f"{df['Latency (s)'].mean():.2f}s")
        m3.metric("Total Tokens", df["Total Tokens"].sum())
    else:
        st.info("No requests executed in this session yet.")

st.divider()

# --- Section 3: Latency Visualization Chart ---
if len(st.session_state.history) > 1:
    st.subheader(" Provider Response Latency Analytics")
    df_chart = pd.DataFrame(st.session_state.history)
    fig = px.bar(
        df_chart,
        x="Timestamp",
        y="Latency (s)",
        color="Provider",
        title="Round-Trip Request Latency per Provider Execution",
        text="Latency (s)"
    )
    st.plotly_chart(fig, use_container_width=True)