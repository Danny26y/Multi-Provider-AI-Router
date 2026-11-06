import time
from typing import Tuple, Union

import httpx
from pydantic import ValidationError

from app.core.circuit_breaker import circuit_breaker
from app.schemas.requests import ChatCompletionRequest, ChatCompletionResponse
from config.settings import settings


class MultiProviderClient:
    def __init__(self):
        self.mock_fallback_enabled = settings.mock_fallback_enabled
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
        diagnostics = []

        for provider_name, config in self.provider_configs.items():
            if config.get("is_mock"):
                if not self.mock_fallback_enabled:
                    diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "fallback_disabled"})
                    continue
                diagnostics.append({"provider": provider_name, "outcome": "success", "category": "mock_fallback"})
                return self._generate_mock_response(request, provider_name, diagnostics)

            if request.model and request.model != config["default_model"]:
                diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "model_not_supported"})
                continue
            if not circuit_breaker.is_available(provider_name):
                diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "circuit_open"})
                continue
            if not config.get("api_key"):
                diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "missing_credentials"})
                continue

            started_at = time.monotonic()
            success, response_or_status, category, retryable = await self._send_http_request(config, request)
            latency = round(time.monotonic() - started_at, 3)

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
                except (AttributeError, TypeError, ValidationError, ValueError):
                    if retryable:
                        circuit_breaker.record_failure(provider_name)
                    diagnostics.append({"provider": provider_name, "outcome": "failed", "category": "invalid_response"})
                    continue

                circuit_breaker.record_success(provider_name)
                return response

            if retryable:
                circuit_breaker.record_failure(provider_name, force_open=category == "rate_limited")
            diagnostics.append({"provider": provider_name, "outcome": "failed", "category": category})

        raise RuntimeError("No eligible AI provider produced a valid response.")

    def _build_response(
        self,
        response_data: dict,
        config: dict,
        provider_name: str,
        request: ChatCompletionRequest,
        latency: float,
        diagnostics: list[dict],
    ) -> ChatCompletionResponse:
        choices = response_data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Upstream response must include at least one choice.")

        raw_usage = response_data.get("usage") or {}
        if not isinstance(raw_usage, dict):
            raise ValueError("Upstream usage must be an object when provided.")
        prompt_tokens = raw_usage.get("prompt_tokens", 0)
        completion_tokens = raw_usage.get("completion_tokens", 0)
        total_tokens = raw_usage.get("total_tokens", prompt_tokens + completion_tokens)

        if total_tokens == 0:
            output_text = choices[0].get("message", {}).get("content", "")
            if not isinstance(output_text, str):
                raise ValueError("Upstream choice content must be text.")
            prompt_text = "".join(message.content for message in request.messages)
            prompt_tokens = max(1, len(prompt_text) // 4)
            completion_tokens = max(1, len(output_text) // 4)
            total_tokens = prompt_tokens + completion_tokens

        return ChatCompletionResponse(
            id=response_data.get("id", f"chatcmpl-{int(time.time())}"),
            created=response_data.get("created", int(time.time())),
            model=response_data.get("model", config["default_model"]),
            choices=choices,
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
    ) -> Tuple[bool, Union[dict, int], str, bool]:
        url = f"{config['base_url']}/chat/completions"
        headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
        payload = {
            "model": request.model or config["default_model"],
            "messages": [message.model_dump() for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

        async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT_SECONDS) as client:
            try:
                response = await client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException:
                return False, 504, "timeout", True
            except httpx.HTTPError:
                return False, 502, "connection_error", True

        if response.status_code == 200:
            try:
                return True, response.json(), "success", True
            except ValueError:
                return False, 502, "invalid_response", True
        if response.status_code == 429:
            return False, 429, "rate_limited", True
        if 500 <= response.status_code <= 599:
            return False, response.status_code, "server_error", True
        if response.status_code in {401, 403}:
            return False, response.status_code, "auth_error", False
        return False, response.status_code, "invalid_request", False

    def _generate_mock_response(
        self,
        request: ChatCompletionRequest,
        provider_name: str,
        diagnostics: list[dict],
    ) -> ChatCompletionResponse:
        return ChatCompletionResponse(
            id=f"chatcmpl-mock-{int(time.time())}",
            created=int(time.time()),
            model="mock-local-fallback",
            provider_used=provider_name,
            fallback_used=True,
            routing_diagnostics=diagnostics,
            latency_seconds=0.0,
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
