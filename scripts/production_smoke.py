"""Opt-in LIVE gateway acceptance. Prints outcomes only, never response text or secrets.

Run: python -m scripts.production_smoke
Requires SMOKE_ROUTER_URL, ROUTER_API_KEY and METRICS_API_KEY in the environment.
No deployment or configuration changes are made. Inference may consume provider quota.
"""

import os

import httpx

from app.schemas.requests import ChatCompletionResponse
from app.services.response_validation import validate_response
from config.settings import settings


def main() -> int:
    url = os.getenv("SMOKE_ROUTER_URL", "").rstrip("/")
    if not url.startswith("https://") or not settings.ROUTER_API_KEY or not settings.METRICS_API_KEY:
        print("LIVE SKIPPED: HTTPS SMOKE_ROUTER_URL and router/metrics credentials required.")
        return 1
    passed = failed = 0

    def check(name, action):
        nonlocal passed, failed
        try:
            assert action()
        except Exception:
            # Exception messages may contain response inputs, URLs, or credentials.
            failed += 1
            print("LIVE FAIL:", name)
        else:
            passed += 1
            print("LIVE PASS:", name)

    with httpx.Client(base_url=url, timeout=75, follow_redirects=False, trust_env=False) as client:
        for path in ("/health/live", "/health/ready"):
            check(path, lambda path=path: client.get(path).status_code == 200)
        payload = {"messages": [{"role": "user", "content": "Reply with one short greeting."}], "max_tokens": 1024}
        check("invalid router key", lambda: client.post(
            "/v1/chat/completions", json=payload, headers={"X-API-Key": "invalid-smoke-key"},
        ).status_code == 401)
        check("metrics protected", lambda: client.get("/metrics").status_code == 401)
        check("metrics authenticated", lambda: client.get(
            "/metrics", headers={"X-Metrics-Key": settings.METRICS_API_KEY},
        ).status_code == 200)

        def inference(provider, model):
            response = client.post("/v1/chat/completions", json={**payload, "model": model},
                                   headers={"X-API-Key": settings.ROUTER_API_KEY})
            response.raise_for_status()
            body = response.json()
            validate_response(body, model, settings.PROVIDER_MODEL_ALIASES.get(provider, {}).get(model, []))
            result = ChatCompletionResponse.model_validate(body)
            return result.provider_used == provider and not result.fallback_used and all(
                set(choice["message"]) == {"role", "content"} for choice in body["choices"]
            )

        for provider, model in (("groq_fast", settings.GROQ_FAST_MODEL), ("nvidia", settings.NVIDIA_DEFAULT_MODEL),
                                ("groq_reasoning", settings.GROQ_REASONING_MODEL)):
            check(provider, lambda provider=provider, model=model: inference(provider, model))
    print(f"LIVE gateway results: {passed} passed, {failed} failed. Controlled fallback/failure checks remain manual.")
    return int(failed > 0)


if __name__ == "__main__":
    raise SystemExit(main())
