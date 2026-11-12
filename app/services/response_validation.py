"""Validate provider wire responses before response defaults or coercion can hide errors."""


class UpstreamResponseError(ValueError):
    def __init__(self, category: str = "invalid_response"):
        # Never carry upstream content, model names, or validation input in errors.
        super().__init__(category)
        self.category = category


def validate_response(data: object, expected_model: str, aliases: list[str]) -> dict:
    if not isinstance(data, dict):
        raise UpstreamResponseError()
    if "id" in data and (not isinstance(data["id"], str) or not data["id"].strip()):
        raise UpstreamResponseError()
    if "created" in data and (type(data["created"]) is not int or data["created"] < 0):
        raise UpstreamResponseError()
    if "object" in data and data["object"] != "chat.completion":
        raise UpstreamResponseError()
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise UpstreamResponseError()
    model = data.get("model")
    if not isinstance(model, str) or not model.strip():
        raise UpstreamResponseError()
    if model not in [expected_model, *aliases]:
        raise UpstreamResponseError("model_mismatch")

    indexes = set()
    for choice in choices:
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise UpstreamResponseError()
        if "index" in choice:
            index = choice["index"]
            if type(index) is not int or index < 0 or index in indexes:
                raise UpstreamResponseError()
            indexes.add(index)
        message = choice["message"]
        if message.get("role") != "assistant":
            raise UpstreamResponseError()
        if "content" not in message:
            raise UpstreamResponseError()
        reason = choice.get("finish_reason")
        if not isinstance(reason, str) or reason not in {
            "stop", "length", "content_filter", "tool_calls", "function_call",
        }:
            raise UpstreamResponseError()
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise UpstreamResponseError()
        if not content or not content.strip() or reason not in {"stop", "length"}:
            raise UpstreamResponseError("unusable_output")

    usage = data.get("usage")
    if usage is not None:
        if not isinstance(usage, dict):
            raise UpstreamResponseError()
        counters = [usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")]
        if any(type(value) is not int or value < 0 for value in counters):
            raise UpstreamResponseError()
        if counters[0] + counters[1] != counters[2]:
            raise UpstreamResponseError()
        for field, budget in (("prompt_tokens_details", counters[0]), ("completion_tokens_details", counters[1])):
            details = usage.get(field)
            if details is None:
                continue
            if not isinstance(details, dict):
                raise UpstreamResponseError()
            for key, value in details.items():
                if key.endswith("_tokens") and (
                    type(value) is not int or value < 0 or value > budget
                ):
                    raise UpstreamResponseError()
    return data
