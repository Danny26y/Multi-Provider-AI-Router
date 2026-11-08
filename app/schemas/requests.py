from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config.settings import settings


MessageRole = Literal["system", "user", "assistant"]
MAX_MESSAGE_CONTENT_CHARS = 20_000
MAX_REQUEST_CONTENT_CHARS = 100_000
MAX_REQUEST_BODY_BYTES = 1_000_000


class UsageStats(BaseModel):
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    estimated_cost_usd: float = Field(default=0.0)
    token_usage_is_estimated: bool = False
    cost_is_estimated: bool = True


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: MessageRole
    content: Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_CONTENT_CHARS)]


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Optional[Annotated[str, Field(min_length=1, max_length=128)]] = None
    messages: Annotated[list[ChatMessage], Field(min_length=1, max_length=100)]
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.7
    max_tokens: Annotated[int, Field(ge=1, le=settings.MAX_OUTPUT_TOKENS)] = 1024
    stream: bool = False

    @model_validator(mode="after")
    def validate_total_content_size(self) -> "ChatCompletionRequest":
        if self.model and self.model not in settings.supported_models:
            raise ValueError("Requested model is not configured.")
        if sum(len(message.content) for message in self.messages) > MAX_REQUEST_CONTENT_CHARS:
            raise ValueError(f"Combined message content must not exceed {MAX_REQUEST_CONTENT_CHARS} characters.")
        return self


class ChatCompletionResponseChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: Optional[str] = "stop"


class ProviderDiagnostic(BaseModel):
    provider: str
    outcome: str
    category: Optional[str] = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionResponseChoice]
    provider_used: str
    fallback_used: bool = False
    routing_diagnostics: list[ProviderDiagnostic] = Field(default_factory=list)
    latency_seconds: float = 0.0
    usage: UsageStats = Field(default_factory=UsageStats)
