import time
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()


@app.post("/v1/chat/completions")
@app.post("/{scenario}/v1/chat/completions")
async def chat_completions(
    request: Request,
    scenario: Literal["success", "outage", "reasoning", "truncated"] = "success",
):
    """Loopback demo only: never forward requests, store prompts, or inspect keys."""
    if scenario == "outage":
        return JSONResponse(status_code=503, content={"error": "simulated-upstream-private-body"})
    payload = await request.json()
    return {
        "id": f"mock-{int(time.time())}",
        "created": int(time.time()),
        "model": payload["model"],
        "choices": [{
            "message": {"role": "assistant", "content": None if scenario == "reasoning" else "mock response"},
            "finish_reason": "length" if scenario in {"reasoning", "truncated"} else "stop",
        }],
        "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
    }
