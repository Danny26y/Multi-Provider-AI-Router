import hmac
import logging

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from app.core.circuit_breaker import circuit_breaker
from app.schemas.requests import MAX_REQUEST_BODY_BYTES, ChatCompletionRequest, ChatCompletionResponse
from app.services.provider_client import MultiProviderClient
from config.settings import settings


app = FastAPI(
    title="Enterprise AI Multi-Provider Proxy",
    description="AI routing proxy with failover controls and circuit breakers",
    version="1.0.0",
)

provider_client = MultiProviderClient()
logger = logging.getLogger(__name__)
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
