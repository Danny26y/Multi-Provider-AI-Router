
import os
import json
import time
import httpx
from dotenv import load_dotenv

load_dotenv()

model = "nvidia/nemotron-3.5-lightning-30b-a3b"

start = time.perf_counter()

with httpx.Client(timeout=20.0) as client:
    response = client.post(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "What is an API gateway? Answer in one sentence."
                }
            ],
            "max_tokens": 1024,
            "temperature": 0.2,
            "stream": False,
        },
    )

elapsed = time.perf_counter() - start

print("HTTP status:", response.status_code)
print("Latency:", round(elapsed, 2), "seconds")

if response.is_success:
    data = response.json()
    choice = data["choices"][0]
    message = choice.get("message", {})

    print("\nContent:", message.get("content"))
    print("Finish reason:", choice.get("finish_reason"))
    print("Message fields:", list(message.keys()))
    print("Usage:", json.dumps(data.get("usage"), indent=2))

    reasoning = message.get("reasoning_content")
    if reasoning is not None:
        print("Reasoning field present:", True)
else:
    print("Error:", response.text[:500])
