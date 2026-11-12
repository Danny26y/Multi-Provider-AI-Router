import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Union

import httpx
from pydantic import ValidationError

from app.core.circuit_breaker import CircuitAdmission, circuit_breaker
from app.core.logging import log_event
from app.core.metrics import (
    COMPLETIONS,
    PROVIDER_FAILURES,
    PROVIDER_LATENCY,
    ROUTING_LATENCY,
    provider_label,
    record_completion,
)
from app.schemas.requests import ChatCompletionRequest, ChatCompletionResponse
from app.services.response_validation import UpstreamResponseError, validate_response
from app.services.routing_errors import RoutingError, routing_diagnostics
from config.settings import settings

logger = logging.getLogger("router.provider")

HEALTH_FAILURES = {"timeout", "connection_error", "rate_limited", "server_error", "invalid_response"}


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
            "nvidia": {
                "base_url": settings.NVIDIA_BASE_URL,
                "api_key": settings.NVIDIA_API_KEY,
                "default_model": settings.NVIDIA_DEFAULT_MODEL,
                "allowed_models": settings.NVIDIA_ALLOWED_MODELS,
                "default_max_tokens": settings.NVIDIA_DEFAULT_MAX_TOKENS,
                "max_output_tokens": settings.NVIDIA_MAX_OUTPUT_TOKENS,
                "normalize_reasoning": True,
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
            and (provider_name != "nvidia" or settings.NVIDIA_ENABLED)
            and (provider_name != "cerebras_primary" or settings.CEREBRAS_ENABLED)
        }
        for name, config in self.provider_configs.items():
            config["model_aliases"] = settings.PROVIDER_MODEL_ALIASES.get(name, {})

    async def start(self) -> None:
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(follow_redirects=False, trust_env=False)

    async def close(self) -> None:
        if self.http_client is not None:
            await self.http_client.aclose()
            self.http_client = None

    def is_ready(self) -> bool:
        has_configured_remote_provider = any(
            config.get("api_key") and any(config.get("allowed_models", [config.get("default_model")]))
            for config in self.provider_configs.values()
            if not config.get("is_mock")
        )
        has_configured_mock = self.mock_fallback_enabled and any(
            config.get("is_mock") for config in self.provider_configs.values()
        )
        return bool(settings.ROUTER_API_KEY) and (has_configured_remote_provider or has_configured_mock)

    async def execute_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        started = time.monotonic()
        outcome = "failed"
        try:
            response = await self._execute_chat_completion(request)
            record_completion(response)
            outcome = "success"
            return response
        except asyncio.CancelledError:
            outcome = "cancelled"
            COMPLETIONS.labels(provider="none", outcome=outcome).inc()
            raise
        except Exception as error:
            outcome = "deadline_exceeded" if isinstance(error, RoutingError) and error.code == "deadline_exceeded" else "failed"
            COMPLETIONS.labels(provider="none", outcome=outcome).inc()
            raise
        finally:
            ROUTING_LATENCY.labels(outcome=outcome).observe(max(0, time.monotonic() - started))

    async def _execute_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        diagnostics = []
        deadline = time.monotonic() + settings.TOTAL_REQUEST_TIMEOUT_SECONDS

        for provider_name, config in self.provider_configs.items():
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "deadline_exceeded"})
                break
            if config.get("is_mock"):
                if not self.mock_fallback_enabled or request.model:
                    diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "fallback_disabled"})
                    continue
                diagnostics.append({"provider": provider_name, "outcome": "success", "category": "mock_fallback"})
                return self._generate_mock_response(provider_name, diagnostics)

            selected_model = request.model or config["default_model"]
            if selected_model not in config.get("allowed_models", [config["default_model"]]):
                diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "model_not_supported"})
                continue
            if not config.get("api_key"):
                diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "missing_credentials"})
                continue
            admission = circuit_breaker.acquire(provider_name)
            if admission is None:
                diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "circuit_open"})
                continue

            try:
                started_at = time.monotonic()
                remaining_seconds = deadline - started_at
                if remaining_seconds <= 0:
                    diagnostics.append({"provider": provider_name, "outcome": "skipped", "category": "deadline_exceeded"})
                    break
                result = await self._send_http_request(config, request, remaining_seconds)
                latency = round(time.monotonic() - started_at, 3)
                PROVIDER_LATENCY.labels(provider=provider_label(provider_name)).observe(max(0, latency))

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
                    except (AttributeError, TypeError, ValidationError, ValueError, OverflowError, RecursionError) as error:
                        category = error.category if isinstance(error, UpstreamResponseError) else "invalid_response"
                        self._handle_failure(provider_name, category, admission=admission)
                        diagnostics.append({"provider": provider_name, "outcome": "failed", "category": category})
                        continue

                    if time.monotonic() >= deadline:
                        diagnostics.append({"provider": provider_name, "outcome": "failed", "category": "deadline_exceeded"})
                        break
                    circuit_breaker.record_success(provider_name, admission=admission)
                    log_event(logger, logging.INFO, "provider_request_succeeded", provider=provider_name, latency_seconds=latency)
                    return response

                _, _, category, _, retry_after_seconds = result
                self._handle_failure(provider_name, category, retry_after_seconds, admission=admission)
                diagnostics.append({"provider": provider_name, "outcome": "failed", "category": category})
            finally:
                # Covers caller cancellation, unexpected exceptions, and inconclusive probes.
                circuit_breaker.record_neutral(provider_name, admission=admission)

        raise RoutingError(diagnostics, deadline_exceeded=time.monotonic() >= deadline)

    def _handle_failure(
        self, provider_name: str, category: str, retry_after_seconds: Optional[float] = None,
        *, admission: CircuitAdmission,
    ) -> None:
        affects_health = category in HEALTH_FAILURES
        if affects_health:
            circuit_breaker.record_failure(
                provider_name,
                force_open=category == "rate_limited",
                retry_after_seconds=retry_after_seconds,
                admission=admission,
            )
        else:
            circuit_breaker.record_neutral(provider_name, admission=admission)
        self._record_provider_failure(provider_name, category, affects_health)

    def _record_provider_failure(self, provider_name: str, category: str, retryable: bool) -> None:
        PROVIDER_FAILURES.labels(
            provider=provider_label(provider_name),
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
        expected_model = request.model or config["default_model"]
        response_data = validate_response(
            response_data, expected_model, config.get("model_aliases", {}).get(expected_model, []),
        )
        choices = response_data["choices"]
        raw_usage = response_data.get("usage")
        has_actual_usage = raw_usage is not None
        if has_actual_usage:
            prompt_tokens = raw_usage["prompt_tokens"]
            completion_tokens = raw_usage["completion_tokens"]
            total_tokens = raw_usage["total_tokens"]
        else:
            output_text = choices[0]["message"]["content"]
            prompt_text = "".join(message.content for message in request.messages)
            prompt_tokens = max(1, len(prompt_text) // 4)
            completion_tokens = max(1, len(output_text) // 4)
            total_tokens = prompt_tokens + completion_tokens

        # Optional provider metadata and internal reasoning are not public assistant content.
        choices = [
            {**choice, "message": {"role": "assistant", "content": choice["message"]["content"]}}
            for choice in choices
        ]
        model = response_data["model"]
        price_per_million = settings.MODEL_PRICES_USD_PER_MILLION_TOKENS.get(model, 0.0)
        diagnostics_with_details = routing_diagnostics(diagnostics)
        return ChatCompletionResponse(
            id=response_data.get("id", f"chatcmpl-{int(time.time())}"),
            created=response_data.get("created", int(time.time())),
            model=model,
            choices=choices,
            provider_used=provider_name,
            fallback_used=any(item.fallback_used for item in diagnostics_with_details),
            routing_diagnostics=diagnostics_with_details,
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
            return False, 500, "provider_configuration_error", False, None

        url = f"{config['base_url'].rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json", "Accept-Encoding": "identity"}
        output_tokens = request.max_tokens
        if "max_tokens" not in request.model_fields_set:
            output_tokens = config.get("default_max_tokens", output_tokens)
        output_tokens = min(output_tokens, config.get("max_output_tokens", settings.MAX_OUTPUT_TOKENS))
        payload = {
            "model": request.model or config["default_model"],
            "messages": [message.model_dump() for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": output_tokens,
        }
        timeout = httpx.Timeout(
            connect=min(settings.REQUEST_CONNECT_TIMEOUT_SECONDS, remaining_seconds),
            read=min(settings.REQUEST_READ_TIMEOUT_SECONDS, remaining_seconds),
            write=min(settings.REQUEST_WRITE_TIMEOUT_SECONDS, remaining_seconds),
            pool=min(settings.REQUEST_POOL_TIMEOUT_SECONDS, remaining_seconds),
        )

        async def exchange() -> httpx.Response:
            async with self.http_client.stream(
                "POST", url, headers=headers, json=payload, timeout=timeout, follow_redirects=False,
            ) as upstream:
                if upstream.status_code != 200:
                    # Classification needs status/Retry-After only; discard raw error bodies.
                    return httpx.Response(upstream.status_code, headers={"Retry-After": upstream.headers.get("Retry-After", "")})
                body = bytearray()
                async for chunk in upstream.aiter_bytes(chunk_size=65536):
                    if len(body) + len(chunk) > settings.MAX_UPSTREAM_RESPONSE_BYTES:
                        raise UpstreamResponseError()
                    body.extend(chunk)
                # aiter_bytes has already decoded Content-Encoding; do not decode it twice.
                return httpx.Response(200, content=bytes(body))

        try:
            response = await asyncio.wait_for(
                exchange(),
                timeout=remaining_seconds,
            )
        except UpstreamResponseError:
            return False, 502, "invalid_response", True, None
        except (httpx.InvalidURL, httpx.UnsupportedProtocol, ValueError):
            return False, 500, "provider_configuration_error", False, None
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return False, 504, "timeout", True, None
        except httpx.HTTPError:
            return False, 502, "connection_error", True, None

        if response.status_code == 200:
            try:
                return True, response.json(), "success", True, None
            except (ValueError, RecursionError, OverflowError):
                return False, 502, "invalid_response", True, None
        if response.status_code == 429:
            return False, 429, "rate_limited", True, self._parse_retry_after(response.headers.get("Retry-After"))
        if 500 <= response.status_code <= 599:
            return False, response.status_code, "server_error", True, None
        if response.status_code in {401, 403}:
            return False, response.status_code, "auth_error", False, None
        if response.status_code == 404:
            return False, 404, "unsupported_model", False, None
        if response.status_code == 408:
            return False, 408, "timeout", True, None
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
        if not math.isfinite(retry_after) or retry_after <= 0:
            return None
        return min(retry_after, settings.MAX_UPSTREAM_RETRY_AFTER_SECONDS)

    def _generate_mock_response(self, provider_name: str, diagnostics: list[dict]) -> ChatCompletionResponse:
        return ChatCompletionResponse(
            id=f"chatcmpl-mock-{int(time.time())}",
            created=int(time.time()),
            model="mock-local-fallback",
            provider_used=provider_name,
            fallback_used=True,
            routing_diagnostics=routing_diagnostics(diagnostics),
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
