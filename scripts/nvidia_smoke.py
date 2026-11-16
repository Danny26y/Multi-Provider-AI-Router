"""Opt-in live NVIDIA probe. Only runs when invoked directly."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.requests import ChatCompletionRequest  # noqa: E402
from app.services.provider_client import MultiProviderClient  # noqa: E402
from app.services.routing_errors import RoutingError  # noqa: E402


async def probe() -> int:
    client = MultiProviderClient()
    config = client.provider_configs.get("nvidia")
    if not config or not config.get("api_key"):
        print("LIVE NVIDIA SKIPPED: enabled provider and NVIDIA_API_KEY required.")
        return 1
    client.provider_configs = {"nvidia": config}
    client.mock_fallback_enabled = False
    await client.start()
    try:
        response = await client.execute_chat_completion(ChatCompletionRequest(
            messages=[{"role": "user", "content": "Reply with one short greeting."}],
            max_tokens=128,
        ))
        print("LIVE NVIDIA PASS: validated visible assistant completion; finish:", response.choices[0].finish_reason)
        return 0
    except RoutingError as error:
        print("LIVE NVIDIA FAIL:", error.code, [item.category for item in error.diagnostics])
        return 1
    finally:
        await client.close()


def main() -> int:
    return asyncio.run(probe())


if __name__ == "__main__":
    sys.exit(main())
