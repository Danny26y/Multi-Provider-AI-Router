import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, status
from app.schemas.requests import ChatCompletionRequest, ChatCompletionResponse
from app.services.provider_client import MultiProviderClient
from app.core.circuit_breaker import circuit_breaker

app = FastAPI(
    title="Enterprise AI Multi-Provider Proxy",
    description="High availability routing proxy with automatic rate-limits failovers and circuit breaker",
    version="1.0.0"
)

provider_client = MultiProviderClient()

@app.get("/health")
async def health_check():
    return{
        "status": "online",
        "circuit_breaker_states": circuit_breaker._states
    }
@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: ChatCompletionRequest):
    try:
        response = await provider_client.execute_chat_completion(request)
        return response
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"All configured AI providers failed: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)