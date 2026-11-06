import hmac
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

import asyncio

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from app.core.circuit_breaker import circuit_breaker
from app.schemas.requests import MAX_REQUEST_BODY_BYTES, ChatCompletionRequest, ChatCompletionResponse
from app.services.provider_client import MultiProviderClient
from config.settings import settings


logger = logging.getLogger(__name__)
provider_client = MultiProviderClient()


class ClientRequestLimiter:
    def __init__(self, requests_per_minute: int, max_concurrent_requests: int):
        self.requests_per_minute = requests_per_minute
        self._timestamps: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)

    async def check_rate_limit(self, client_id: str) -> int | None:
        now = time.monotonic()
        async with self._lock:
            timestamps = self._timestamps[client_id]
            while timestamps and now - timestamps[0] >= 60:
                timestamps.popleft()
            if len(timestamps) >= self.requests_per_minute:
                return max(1, int(60 - (now - timestamps[0])))
            timestamps.append(now)
            return None

    async def acquire(self) -> None:
        await self._semaphore.acquire()

    def release(self) -> None:
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

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@app.middleware("http")
async def limit_chat_request_size(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/v1/chat/completions":
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                request_size = int(content_length)
            except ValueError:
                return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": "Invalid Content-Length."})
            if request_size > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": "Request body is too large."},
                )
    return await call_next(request)


def require_api_key(api_key: str | None = Depends(api_key_header)) -> None:
    if not settings.ROUTER_API_KEY:
        logger.error("Chat completion authentication is not configured.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat completion service is not configured.",
        )
    if not api_key or not hmac.compare_digest(api_key, settings.ROUTER_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )


async def enforce_client_limits(request: Request):
    client_id = request.client.host if request.client else "unknown"
    retry_after_seconds = await rate_limiter.check_rate_limit(client_id)
    if retry_after_seconds is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded.",
            headers={"Retry-After": str(retry_after_seconds)},
        )
    await rate_limiter.acquire()
    try:
        yield
    finally:
        rate_limiter.release()


@app.get("/health/live")
async def liveness_check():
    return {"status": "online"}


@app.get("/health")
async def health_check():
    return {
        "status": "online",
        "circuit_breaker_states": circuit_breaker.snapshot(),
    }


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
    except Exception:
        logger.error("Chat completion failed after eligible providers were attempted.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No AI provider is currently available.",
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
