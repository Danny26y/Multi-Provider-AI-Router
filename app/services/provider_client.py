import time
from typing import Tuple, Union

import httpx
from pydantic import ValidationError

from app.core.circuit_breaker import circuit_breaker
from app.schemas.requests import ChatCompletionRequest, ChatCompletionResponse
from config.settings import settings


class MultiProviderClient:
    def __init__(self):
        self.provider_configs = {
            "groq_fast": {
                "base_url": settings.GROQ_BASE_URL,
                "api_key": settings.GROQ_API_KEY,
                "default_model": "llama-3.1-8b-instant",
            },
            "cerebras_primary": {
                "base_url": settings.CEREBRAS_BASE_URL,
                "api_key": settings.CEREBRAS_API_KEY,
                "default_model": "zai-glm-4.7",
            },
            "groq_reasoning": {
                "base_url": settings.GROQ_BASE_URL,
                "api_key": settings.GROQ_API_KEY,
                "default_model": "llama-3.3-70b-versatile",
            },
            "mock_fallback": {"is_mock": True},
        }

    async def execute_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        attempted_providers = []
        diagnostics = []

        for provider_name, config in self.provider_configs.items():
            if not circuit_breaker.is_available(provider_name):
                diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "circuit_open"})
                continue

            if not config.get("is_mock") and not config.get("api_key"):
                diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "missing_credentials"})
                continue

            attempted_providers.append(provider_name)
            print(f"[ROUTER]: Routing request to '{provider_name}'.")
            started_at = time.time()

            if config.get("is_mock"):
                diagnostics.append({"provider": provider_name, "outcome": "success", "category": "mock_fallback"})
                return self._generate_mock_response(
                    request,
                    provider_name,
                    latency=round(time.time() - started_at, 3),
                    diagnostics=diagnostics,
                )

            success, response_or_status, category = await self._send_http_request(config, request)
            latency = round(time.time() - started_at, 3)

            if success:
                try:
                    response = self._build_response(
                        response_or_status,
                        config,
                        provider_name,
                        request,
                        latency,
                        diagnostics + [{"provider": provider_name, "outcome": "success"}],
                    )
                except (AttributeError, TypeError, ValidationError):
                    circuit_breaker.record_failure(provider_name, error_code=502)
                    diagnostics.append({"provider": provider_name, "outcome": "failed", "category": "invalid_response"})
                    continue

                circuit_breaker.record_success(provider_name)
                return response

            circuit_breaker.record_failure(provider_name, error_code=response_or_status)
            diagnostics.append({"provider": provider_name, "outcome": "failed", "category": category})

        raise RuntimeError(f"All configured AI providers failed. Tried: {attempted_providers}")

    def _build_response(
        self,
        response_data: dict,
        config: dict,
        provider_name: str,
        request: ChatCompletionRequest,
        latency: float,
        diagnostics: list[dict],
    ) -> ChatCompletionResponse:
        raw_usage = response_data.get("usage") or {}
        prompt_tokens = raw_usage.get("prompt_tokens", 0)
        completion_tokens = raw_usage.get("completion_tokens", 0)
        total_tokens = raw_usage.get("total_tokens", prompt_tokens + completion_tokens)

        if total_tokens == 0:
            output_text = ""
            if response_data.get("choices"):
                output_text = response_data["choices"][0].get("message", {}).get("content", "")
            prompt_text = "".join(message.content for message in request.messages)
            prompt_tokens = max(1, len(prompt_text) // 4)
            completion_tokens = max(1, len(output_text) // 4)
            total_tokens = prompt_tokens + completion_tokens

        return ChatCompletionResponse(
            id=response_data.get("id", f"chatcmpl-{int(time.time())}"),
            created=response_data.get("created", int(time.time())),
            model=response_data.get("model", config["default_model"]),
            choices=response_data.get("choices", []),
            provider_used=provider_name,
            fallback_used=False,
            routing_diagnostics=diagnostics,
            latency_seconds=latency,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "estimated_cost_usd": round((total_tokens / 1_000_000) * 0.05, 6),
            },
        )

    async def _send_http_request(
        self, config: dict, request: ChatCompletionRequest
    ) -> Tuple[bool, Union[dict, int], str]:
        url = f"{config['base_url']}/chat/completions"
        headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
        payload = {
            "model": config["default_model"],
            "messages": [message.model_dump() for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

        async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT_SECONDS) as client:
            try:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 200:
                    try:
                        return True, response.json(), "success"
                    except ValueError:
                        print(f"[PROVIDER ERROR]: Invalid JSON response from {config['default_model']}.")
                        return False, 502, "invalid_response"

                print(f"[PROVIDER HTTP ERROR]: {config['default_model']} returned status {response.status_code}.")
                return False, response.status_code, "http_error"
            except httpx.TimeoutException:
                print(f"[PROVIDER ERROR]: Timeout calling {config['default_model']}.")
                return False, 504, "timeout"
            except httpx.HTTPError:
                print(f"[PROVIDER ERROR]: Connection error calling {config['default_model']}.")
                return False, 502, "connection_error"

    def _generate_mock_response(
        self,
        request: ChatCompletionRequest,
        provider_name: str,
        latency: float = 0.0,
        diagnostics: list[dict] | None = None,
    ) -> ChatCompletionResponse:
        return ChatCompletionResponse(
            id=f"chatcmpl-mock-{int(time.time())}",
            created=int(time.time()),
            model="mock-local-fallback",
            provider_used=provider_name,
            fallback_used=True,
            routing_diagnostics=diagnostics or [],
            latency_seconds=latency,
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "[SYSTEM FALLBACK]: All eligible remote providers were unavailable. This response was served by the local mock fallback.",
                    },
                    "finish_reason": "stop",
                }
            ],
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 25,
                "total_tokens": 35,
                "estimated_cost_usd": 0.0,
            },
        )
