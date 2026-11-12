import asyncio
import hmac
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from app.core.body_limit import RequestBodyLimitMiddleware
from app.core.logging import configure_logging, log_event, request_id_context
from app.core.metrics import (
    CHAT_INFLIGHT,
    CHAT_QUEUED,
    CONCURRENCY_LIMIT,
    HTTP_INFLIGHT,
    HTTP_LATENCY,
    HTTP_REQUESTS,
    LIMIT_REJECTIONS,
    render_metrics,
    request_labels,
)
from app.schemas.requests import ChatCompletionRequest, ChatCompletionResponse
from app.services.provider_client import MultiProviderClient
from app.services.routing_errors import RoutingError
from config.settings import settings

logger = logging.getLogger("router.application")
provider_client = MultiProviderClient()
configure_logging()


class ClientRequestLimiter:
    def __init__(self, requests_per_minute: int, max_concurrent_requests: int):
        self.requests_per_minute = requests_per_minute
        self._timestamps: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._last_prune = 0.0
        CONCURRENCY_LIMIT.set(max_concurrent_requests)

    async def check_rate_limit(self, client_id: str) -> int | None:
        now = time.monotonic()
        async with self._lock:
            if now - self._last_prune >= 60:
                self._timestamps = defaultdict(deque, {
                    key: values for key, values in self._timestamps.items() if values and now - values[-1] < 60
                })
                self._last_prune = now
            if client_id not in self._timestamps and len(self._timestamps) >= settings.MAX_TRACKED_CLIENTS:
                return 60
            timestamps = self._timestamps[client_id]
            while timestamps and now - timestamps[0] >= 60:
                timestamps.popleft()
            if len(timestamps) >= self.requests_per_minute:
                return max(1, int(60 - (now - timestamps[0])))
            timestamps.append(now)
            return None

    async def acquire(self) -> None:
        CHAT_QUEUED.inc()
        try:
            await asyncio.wait_for(self._semaphore.acquire(), settings.CONCURRENCY_QUEUE_TIMEOUT_SECONDS)
        finally:
            CHAT_QUEUED.dec()
        CHAT_INFLIGHT.inc()

    def release(self) -> None:
        CHAT_INFLIGHT.dec()
        self._semaphore.release()


rate_limiter = ClientRequestLimiter(
    settings.CLIENT_RATE_LIMIT_PER_MINUTE,
    settings.MAX_CONCURRENT_REQUESTS,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await provider_client.start()
    try:
        yield
    finally:
        await provider_client.close()


app = FastAPI(
    title="Enterprise AI Multi-Provider Proxy",
    description="AI routing proxy with failover controls and circuit breakers",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(RequestBodyLimitMiddleware)


@app.exception_handler(RequestValidationError)
async def invalid_request_handler(_request: Request, _error: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": ["body"], "msg": "Invalid request body or parameters.", "type": "value_error"},
    ]})

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@app.middleware("http")
async def instrument_request(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    secrets = (settings.ROUTER_API_KEY, settings.METRICS_API_KEY, settings.GROQ_API_KEY, settings.NVIDIA_API_KEY, settings.CEREBRAS_API_KEY)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) or any(secret and secret in request_id for secret in secrets):
        request_id = str(uuid.uuid4())
    method, path = request_labels(request.method, request.url.path)
    request_id_token = request_id_context.set(request_id)
    started_at = time.monotonic()
    response = None
    HTTP_INFLIGHT.inc()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        status_code = response.status_code if response is not None else status.HTTP_500_INTERNAL_SERVER_ERROR
        duration = time.monotonic() - started_at
        HTTP_INFLIGHT.dec()
        HTTP_REQUESTS.labels(method=method, path=path, status_code=status_code).inc()
        HTTP_LATENCY.labels(method=method, path=path).observe(duration)
        log_event(
            logging.getLogger("router.request"),
            logging.INFO,
            "http_request_completed",
            method=method,
            path=path,
            status_code=status_code,
            latency_seconds=round(duration, 6),
        )
        request_id_context.reset(request_id_token)


def require_api_key(api_key: str | None = Depends(api_key_header)) -> None:
    if not settings.ROUTER_API_KEY:
        log_event(logger, logging.ERROR, "chat_authentication_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat completion service is not configured.",
        )
    if not api_key or not hmac.compare_digest(api_key.encode(), settings.ROUTER_API_KEY.encode()):
        log_event(logger, logging.WARNING, "chat_authentication_failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )


async def enforce_client_limits(request: Request):
    client_id = request.client.host if request.client else "unknown"
    retry_after_seconds = await rate_limiter.check_rate_limit(client_id)
    if retry_after_seconds is not None:
        LIMIT_REJECTIONS.labels(limit="rate").inc()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded.",
            headers={"Retry-After": str(retry_after_seconds)},
        )
    try:
        await rate_limiter.acquire()
    except asyncio.TimeoutError:
        LIMIT_REJECTIONS.labels(limit="concurrency").inc()
        raise HTTPException(status_code=503, detail="Request capacity reached. Retry shortly.", headers={"Retry-After": "1"}) from None
    try:
        yield
    finally:
        rate_limiter.release()


@app.get("/health/live")
async def liveness_check():
    return {"status": "online"}


@app.get("/health/ready")
async def readiness_check():
    if not provider_client.is_ready():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service is not ready.")
    return {"status": "ready"}


@app.get("/health")
async def health_check():
    return {"status": "online"}


@app.get("/metrics")
async def metrics(request: Request):
    if settings.METRICS_API_KEY:
        supplied = request.headers.get("X-Metrics-Key", "")
        if not hmac.compare_digest(supplied.encode(), settings.METRICS_API_KEY.encode()):
            raise HTTPException(status_code=401, detail="Invalid metrics key.")
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    _: None = Depends(require_api_key),
    __: None = Depends(enforce_client_limits),
):
    if request.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Streaming responses are not supported.",
        )
    try:
        return await provider_client.execute_chat_completion(request)
    except RoutingError as error:
        log_event(logger, logging.ERROR, "chat_completion_failed", category=error.code)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "No AI provider is currently available.", "error": error.public_error()},
        )
    except Exception:
        log_event(logger, logging.ERROR, "chat_completion_failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No AI provider is currently available.",
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000)
