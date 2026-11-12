"""Opt-in live NVIDIA probe. Only runs when invoked directly."""

import os
import sys

import httpx
from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    key = os.getenv("NVIDIA_API_KEY")
    if not key:
        print("NVIDIA_API_KEY is required.")
        return 1
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
                + "/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": os.getenv("NVIDIA_DEFAULT_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"),
                    "messages": [{"role": "user", "content": "What is an API gateway? Answer in one sentence."}],
                    "max_tokens": int(os.getenv("NVIDIA_DEFAULT_MAX_TOKENS", "4096")),
                    "stream": False,
                },
            )
        print("HTTP status:", response.status_code)
        return 0 if response.is_success else 1
    except httpx.HTTPError:
        print("NVIDIA request failed (transport error).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
