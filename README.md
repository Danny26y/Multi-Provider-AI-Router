# ⚡ Enterprise Multi-Provider AI Routing Proxy

A high-throughput, OpenAI-compatible **FastAPI proxy gateway** engineered to eliminate single-provider downtime, bypass API rate-limit errors (`429 Too Many Requests`), and optimize LLM inference costs.

The system features dynamic provider health tracking, an automated circuit breaker state machine, real-time token/latency telemetry, and an interactive Streamlit operational control center.

---

## 🏗️ System Architecture & Failover Flow

```text
                        [ Client Application ]
                                  │
                                  ▼
               ┌─────────────────────────────────────┐
               │ FastAPI AI Gateway (/v1/chat/...)    │
               │ • Pydantic Schema Validation         │
               │ • Token & Cost Analytics Middleware  │
               └──────────────────┬────────────────────┘
                                  │
         ┌────────────────────────┼────────────────────────┐
         ▼                        ▼                        ▼
  [ Primary: Groq LPU ]  [ Secondary: Cerebras CS-3 ]  [ Local Resilience ]
  (Ultra-Fast Llama 3.1) (High-Speed Inference)        (Mock/Zero-Cost Fallback)
         │                        │                        │
         └────────────────────────┴────────────────────────┘
                                  │ (On 429 / 5xx / Timeout)
                                  ▼
                      ┌───────────────────────┐
                      │  Circuit Breaker Sync │
                      └───────────────────────┘
```

## 🔑 Key Infrastructure Features

- **Automated Circuit Breaker State Machine** — Tracks consecutive provider HTTP errors (401, 429, 5xx) and opens the circuit (`UNHEALTHY`) to bypass failing endpoints during recovery cooldown windows.
- **OpenAI API Compatibility** — Native support for standard OpenAI SDK payloads (`/v1/chat/completions`), allowing drop-in integration with LangChain, LlamaIndex, or custom apps.
- **Cascading Failover Engine** — Dynamically routes requests across primary (Groq LPU), secondary (Cerebras CS-3), and tertiary providers without dropping active client HTTP sessions.
- **Real-Time Telemetry & Cost Analytics** — Measures round-trip execution latency, prompt/completion token consumption, and estimates USD cost per API call.
- **Streamlit Operational Control Center** — Interactive dashboard for monitoring live provider health badges, reviewing execution logs, and stress-testing failover routes in real time.

## 🛠️ Tech Stack

| Category | Technologies |
|---|---|
| Framework | Python 3.12, FastAPI, Uvicorn, Pydantic v2 |
| HTTP & Async | HTTPX (`httpx.AsyncClient`) |
| Inference Cloud Providers | Groq LPU, Cerebras CS-3 |
| Observability & Analytics | Streamlit, Plotly, Pandas |

## 📁 Repository Structure

```text
multi-provider-ai-router/
│
├── .env.example              # Template environment file
├── .gitignore                # Excludes secrets, venv, and cache
├── requirements.txt          # Project dependencies
├── README.md                 # System documentation
├── main.py                   # FastAPI application entry point
├── dashboard.py              # Streamlit control panel & telemetry UI
├── test_failover.py          # Automated load & failover test suite
│
├── config/
│   └── settings.py           # Centralized environment & server configurations
│
└── app/
    ├── core/
    │   └── circuit_breaker.py # Circuit breaker state tracking engine
    │
    ├── schemas/
    │   └── requests.py        # Pydantic API schemas & usage models
    │
    └── services/
        └── provider_client.py # Multi-provider client & fallback handler
```

## 🚀 Quickstart Guide

### 1. Clone & Set Up Environment

```powershell
git clone https://github.com/Danny26y/Multi-Provider-AI-Router
cd multi-provider-ai-router

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1   # Windows PowerShell
# source venv/bin/activate    # Linux/macOS
```

### 2. Install Dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure API Keys

Create a `.env` file in the project root:

```ini
GROQ_API_KEY=gsk_your_groq_api_key_here
CEREBRAS_API_KEY=csk_your_cerebras_api_key_here
```

### 4. Run the FastAPI Proxy Gateway

```powershell
python main.py
```

The gateway runs at `http://localhost:8000`. You can inspect the interactive OpenAPI documentation at `http://localhost:8000/docs`.

### 5. Launch the Streamlit Monitoring Dashboard

In a separate terminal (with virtual environment activated):

```powershell
streamlit run dashboard.py
```

## 🧪 Testing Failover Execution

To verify that the proxy automatically routes around failing endpoints, run the included load testing suite:

```powershell
python test_failover.py
```

**What happens during the test:**

1. Requests hit the primary provider (`groq_fast`).
2. If `groq_fast` encounters simulated rate limits or errors, the circuit breaker flags it as `UNHEALTHY`.
3. The gateway automatically reroutes subsequent requests to `cerebras_primary` or `mock_fallback` with zero dropped requests.

## 📡 API Example Usage

You can use standard `curl` or `requests` to query the gateway:

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
     -H "Content-Type: application/json" \
     -d '{
       "messages": [
         {"role": "user", "content": "Explain circuit breaker pattern in 2 sentences."}
       ]
     }'
```
