import asyncio
import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Union

import httpx
from pydantic import ValidationError

from app.core.circuit_breaker import circuit_breaker
from app.core.logging import log_event
from app.core.metrics import PROVIDER_FAILURES, PROVIDER_LATENCY
from app.schemas.requests import ChatCompletionRequest, ChatCompletionResponse
from config.settings import settings


logger = logging.getLogger("router.provider")


class MultiProviderClient:
    def __init__(self):
        self.http_client: Optional[httpx.AsyncClient] = None
        self.mock_fallback_enabled = settings.mock_fallback_enabled
        provider_configs = {
            "groq_fast": {
                "base_url": settings.GROQ_BASE_URL,
                "api_key": settings.GROQ_API_KEY,
                "default_model": settings.GROQ_FAST_MODEL,
            },
            "cerebras_primary": {
                "base_url": settings.CEREBRAS_BASE_URL,
                "api_key": settings.CEREBRAS_API_KEY,
                "default_model": settings.CEREBRAS_PRIMARY_MODEL,
            },
            "groq_reasoning": {
                "base_url": settings.GROQ_BASE_URL,
                "api_key": settings.GROQ_API_KEY,
                "default_model": settings.GROQ_REASONING_MODEL,
            },
            "mock_fallback": {"is_mock": True},
        }
        self.provider_configs = {
            provider_name: provider_configs[provider_name]
            for provider_name in settings.PROVIDER_ORDER
            if provider_name in provider_configs
        }

    async def start(self) -> None:
        if self.http_client is None:
            self.http_client = httpx.AsyncClient()

    async def close(self) -> None:
        if self.http_client is not None:
            await self.http_client.aclose()
            self.http_client = None

    def is_ready(self) -> bool:
        has_configured_remote_provider = any(
            config.get("api_key")
            for config in self.provider_configs.values()
            if not config.get("is_mock")
        )
        return bool(settings.ROUTER_API_KEY) and (has_configured_remote_provider or self.mock_fallback_enabled)

    async def execute_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        diagnostics = []
        deadline = time.monotonic() + settings.TOTAL_REQUEST_TIMEOUT_SECONDS

        for provider_name, config in self.provider_configs.items():
            if config.get("is_mock"):
                if not self.mock_fallback_enabled:
                    diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "fallback_disabled"})
                    continue
                diagnostics.append({"provider": provider_name, "outcome": "success", "category": "mock_fallback"})
                return self._generate_mock_response(provider_name, diagnostics)

            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "deadline_exceeded"})
                break
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
            result = await self._send_http_request(config, request, remaining_seconds)
            latency = round(time.monotonic() - started_at, 3)

            if result[0]:
                try:
                    response = self._build_response(
                        result[1],
                        config,
                        provider_name,
                        request,
                        latency,
                        diagnostics + [{"provider": provider_name, "outcome": "success"}],
                    )
                except (AttributeError, TypeError, ValidationError, ValueError):
                    circuit_breaker.record_failure(provider_name)
                    self._record_provider_failure(provider_name, "invalid_response", True)
                    diagnostics.append({"provider": provider_name, "outcome": "failed", "category": "invalid_response"})
                    continue

                circuit_breaker.record_success(provider_name)
                PROVIDER_LATENCY.labels(provider=provider_name).observe(latency)
                log_event(logger, logging.INFO, "provider_request_succeeded", provider=provider_name, latency_seconds=latency)
                return response

            _, _, category, retryable, retry_after_seconds = result
            if retryable:
                circuit_breaker.record_failure(
                    provider_name,
                    force_open=category == "rate_limited",
                    retry_after_seconds=retry_after_seconds,
                )
            self._record_provider_failure(provider_name, category, retryable)
            diagnostics.append({"provider": provider_name, "outcome": "failed", "category": category})

        raise RuntimeError("No eligible AI provider produced a valid response.")

    def _record_provider_failure(self, provider_name: str, category: str, retryable: bool) -> None:
        PROVIDER_FAILURES.labels(
            provider=provider_name,
            category=category,
            retryable=str(retryable).lower(),
        ).inc()
        log_event(
            logger,
            logging.WARNING,
            "provider_request_failed",
            provider=provider_name,
            category=category,
            retryable=retryable,
        )

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
        token_values = [raw_usage.get("prompt_tokens"), raw_usage.get("completion_tokens"), raw_usage.get("total_tokens")]
        has_actual_usage = all(isinstance(value, int) and value >= 0 for value in token_values)
        if has_actual_usage:
            prompt_tokens, completion_tokens, total_tokens = token_values
        else:
            output_text = choices[0].get("message", {}).get("content", "")
            if not isinstance(output_text, str):
                raise ValueError("Upstream choice content must be text.")
            prompt_text = "".join(message.content for message in request.messages)
            prompt_tokens = max(1, len(prompt_text) // 4)
            completion_tokens = max(1, len(output_text) // 4)
            total_tokens = prompt_tokens + completion_tokens

        model = response_data.get("model", config["default_model"])
        price_per_million = settings.MODEL_PRICES_USD_PER_MILLION_TOKENS.get(model, 0.0)
        return ChatCompletionResponse(
            id=response_data.get("id", f"chatcmpl-{int(time.time())}"),
            created=response_data.get("created", int(time.time())),
            model=model,
            choices=choices,
            provider_used=provider_name,
            fallback_used=False,
            routing_diagnostics=diagnostics,
            latency_seconds=latency,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "estimated_cost_usd": round((total_tokens / 1_000_000) * price_per_million, 6),
                "token_usage_is_estimated": not has_actual_usage,
                "cost_is_estimated": True,
            },
        )

    async def _send_http_request(
        self,
        config: dict,
        request: ChatCompletionRequest,
        remaining_seconds: float,
    ) -> tuple[bool, Union[dict, int], str, bool, Optional[float]]:
        if self.http_client is None:
            raise RuntimeError("Provider HTTP client has not been started.")

        url = f"{config['base_url']}/chat/completions"
        headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
        payload = {
            "model": request.model or config["default_model"],
            "messages": [message.model_dump() for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        timeout = httpx.Timeout(
            connect=min(settings.REQUEST_CONNECT_TIMEOUT_SECONDS, remaining_seconds),
            read=min(settings.REQUEST_READ_TIMEOUT_SECONDS, remaining_seconds),
            write=min(settings.REQUEST_WRITE_TIMEOUT_SECONDS, remaining_seconds),
            pool=min(settings.REQUEST_POOL_TIMEOUT_SECONDS, remaining_seconds),
        )
        try:
            response = await asyncio.wait_for(
                self.http_client.post(url, headers=headers, json=payload, timeout=timeout),
                timeout=remaining_seconds,
            )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return False, 504, "timeout", True, None
        except httpx.HTTPError:
            return False, 502, "connection_error", True, None

        if response.status_code == 200:
            try:
                return True, response.json(), "success", True, None
            except ValueError:
                return False, 502, "invalid_response", True, None
        if response.status_code == 429:
            return False, 429, "rate_limited", True, self._parse_retry_after(response.headers.get("Retry-After"))
        if 500 <= response.status_code <= 599:
            return False, response.status_code, "server_error", True, None
        if response.status_code in {401, 403}:
            return False, response.status_code, "auth_error", False, None
        return False, response.status_code, "invalid_request", False, None

    def _parse_retry_after(self, value: Optional[str]) -> Optional[float]:
        if not value:
            return None
        try:
            retry_after = float(value)
        except ValueError:
            try:
                retry_date = parsedate_to_datetime(value)
                retry_after = (retry_date - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                return None
        if retry_after <= 0:
            return None
        return min(retry_after, settings.MAX_UPSTREAM_RETRY_AFTER_SECONDS)

    def _generate_mock_response(self, provider_name: str, diagnostics: list[dict]) -> ChatCompletionResponse:
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
                "token_usage_is_estimated": True,
                "cost_is_estimated": True,
            },
        )
