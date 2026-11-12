"""Bounded response-level counters; no prompts, keys, raw errors, or model labels."""

PROVIDERS = {"groq_fast", "nvidia", "groq_reasoning", "cerebras_primary", "mock_fallback"}


def completion_observation(data) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("provider_used"), str) or data["provider_used"] not in PROVIDERS:
        return None
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    for choice in choices:
        if not isinstance(choice, dict) or choice.get("finish_reason") not in ("stop", "length"):
            return None
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return None
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return None
    if not isinstance(data.get("fallback_used"), bool):
        return None
    return {
        "provider": data["provider_used"], "fallback": data["fallback_used"],
        "truncated": any(choice["finish_reason"] == "length" for choice in choices),
    }
