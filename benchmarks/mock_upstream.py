import time

from fastapi import FastAPI


app = FastAPI()


@app.post("/v1/chat/completions")
async def chat_completions():
    return {
        "id": f"mock-{int(time.time())}",
        "created": int(time.time()),
        "model": "benchmark-model",
        "choices": [{"message": {"role": "assistant", "content": "mock response"}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
    }
