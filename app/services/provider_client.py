# app/services/provider_client.py
import time
import httpx
from typing import Dict, Tuple, Union
from config.settings import settings
from app.schemas.requests import ChatCompletionRequest, ChatCompletionResponse
from app.core.circuit_breaker import circuit_breaker


class MultiProviderClient:
    def __init__(self):
        self.provider_configs = {
            "groq_fast": {
                "base_url": settings.GROQ_BASE_URL,
                "api_key": settings.GROQ_API_KEY,
                "default_model": "llama-3.1-8b-instant"
            },
            "cerebras_primary": {
                "base_url": settings.CEREBRAS_BASE_URL,
                "api_key": settings.CEREBRAS_API_KEY,
                "default_model": "zai-glm-4.7"
            },
            "groq_reasoning": {
                "base_url": settings.GROQ_BASE_URL,
                "api_key": settings.GROQ_API_KEY,
                "default_model": "llama-3.3-70b-versatile"
            },
            "mock_fallback": {
                "is_mock": True
            }
        }

    async def execute_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        attempted_providers = []

        for provider_name, config in self.provider_configs.items():
            if not circuit_breaker.is_available(provider_name):
                continue

            if not config.get("is_mock") and not config.get("api_key"):
                continue

            attempted_providers.append(provider_name)
            print(f"🚀 [ROUTER]: Routing request to '{provider_name}'...")

            t0 = time.time()

            if config.get("is_mock"):
                t1 = time.time()
                return self._generate_mock_response(request, provider_name, latency=round(t1 - t0, 3))

            success, response_or_status = await self._send_http_request(config, request)
            t1 = time.time()
            latency = round(t1 - t0, 3)

            # Inside MultiProviderClient.execute_chat_completion

            if success:
                circuit_breaker.record_success(provider_name)
                response_data = response_or_status

                # Calculate token counts
                raw_usage = response_data.get("usage") or {}
                prompt_tokens = raw_usage.get("prompt_tokens", 0)
                completion_tokens = raw_usage.get("completion_tokens", 0)
                total_tokens = raw_usage.get("total_tokens", prompt_tokens + completion_tokens)

                # Fallback token estimate (~4 chars per token)
                if total_tokens == 0:
                    output_text = ""
                    if "choices" in response_data and len(response_data["choices"]) > 0:
                        output_text = response_data["choices"][0].get("message", {}).get("content", "")

                    prompt_text = "".join([m.content for m in request.messages])
                    prompt_tokens = max(1, len(prompt_text) // 4)
                    completion_tokens = max(1, len(output_text) // 4)
                    total_tokens = prompt_tokens + completion_tokens

                est_cost = (total_tokens / 1_000_000) * 0.05

                # Construct explicit ChatCompletionResponse
                return ChatCompletionResponse(
                    id=response_data.get("id", f"chatcmpl-{int(time.time())}"),
                    created=response_data.get("created", int(time.time())),
                    model=response_data.get("model", config["default_model"]),
                    choices=response_data.get("choices", []),
                    provider_used=provider_name,
                    latency_seconds=latency,
                    usage={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                        "estimated_cost_usd": round(est_cost, 6)
                    }
                )

            else:
                status_code = response_or_status
                circuit_breaker.record_failure(provider_name, error_code=status_code)

        raise RuntimeError(f"All configured AI providers failed. Tried: {attempted_providers}")

    async def _send_http_request(self, config: dict, request: ChatCompletionRequest) -> Tuple[bool, Union[dict, int]]:
        url = f"{config['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": config["default_model"],
            "messages": [msg.model_dump() for msg in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens
        }

        async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT_SECONDS) as client:
            try:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code == 200:
                    return True, res.json()

                print(f"\n❌ [PROVIDER HTTP ERROR] Provider: {config.get('default_model')} | Status: {res.status_code}")
                print(f"   Body: {res.text}\n")
                return False, res.status_code
            except httpx.TimeoutException:
                print(f"⏱️ Timeout connecting to {url}")
                return False, 504
            except Exception as e:
                print(f"❌ Connection error to {url}: {e}")
                return False, 500

    def _generate_mock_response(self, request: ChatCompletionRequest, provider_name: str,
                                latency: float = 0.0) -> ChatCompletionResponse:
        return ChatCompletionResponse(
            id=f"chatcmpl-mock-{int(time.time())}",
            created=int(time.time()),
            model="mock-local-fallback",
            provider_used=provider_name,
            latency_seconds=latency,
            choices=[{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "[SYSTEM FALLBACK]: All remote API providers experienced downtime or rate limits. This response was served by the local resilience proxy fallback."
                },
                "finish_reason": "stop"
            }],
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 25,
                "total_tokens": 35,
                "estimated_cost_usd": 0.0
            }
        )