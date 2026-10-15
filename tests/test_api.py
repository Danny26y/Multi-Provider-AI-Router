import asyncio

import pytest
from fastapi import HTTPException

import main
from app.schemas.requests import ChatCompletionRequest, ChatCompletionResponse


def request(stream=False):
    return ChatCompletionRequest(messages=[{"role": "user", "content": "Hello"}], stream=stream)


def test_streaming_is_rejected_explicitly():
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.chat_completions(request(stream=True)))

    assert error.value.status_code == 400
    assert error.value.detail == "Streaming responses are not supported."


def test_provider_failure_returns_generic_service_error(monkeypatch):
    async def fail(_request):
        raise RuntimeError("secret upstream body")

    monkeypatch.setattr(main.provider_client, "execute_chat_completion", fail)

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.chat_completions(request()))

    assert error.value.status_code == 503
    assert error.value.detail == "No AI provider is currently available."
