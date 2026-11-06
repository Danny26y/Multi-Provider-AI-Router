from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    PROJECT_NAME: str = "Enterprise AI Routing Proxy"
    APP_ENV: Literal["development", "testing", "production"] = "production"

    ROUTER_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    CEREBRAS_API_KEY: Optional[str] = None

    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    CEREBRAS_BASE_URL: str = "https://api.cerebras.ai/v1"
    GROQ_FAST_MODEL: str = "llama-3.1-8b-instant"
    CEREBRAS_PRIMARY_MODEL: str = "zai-glm-4.7"
    GROQ_REASONING_MODEL: str = "llama-3.3-70b-versatile"
    PROVIDER_ORDER: list[str] = ["groq_fast", "cerebras_primary", "groq_reasoning", "mock_fallback"]

    REQUEST_CONNECT_TIMEOUT_SECONDS: float = Field(default=3.0, gt=0)
    REQUEST_READ_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0)
    REQUEST_WRITE_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0)
    REQUEST_POOL_TIMEOUT_SECONDS: float = Field(default=3.0, gt=0)
    TOTAL_REQUEST_TIMEOUT_SECONDS: float = Field(default=20.0, gt=0)
    MAX_UPSTREAM_RETRY_AFTER_SECONDS: float = Field(default=300.0, gt=0)

    CLIENT_RATE_LIMIT_PER_MINUTE: int = Field(default=60, gt=0)
    MAX_CONCURRENT_REQUESTS: int = Field(default=20, gt=0)
    ENABLE_MOCK_FALLBACK: bool = False
    MODEL_PRICES_USD_PER_MILLION_TOKENS: dict[str, float] = {}

    @property
    def mock_fallback_enabled(self) -> bool:
        return self.APP_ENV in {"development", "testing"} and self.ENABLE_MOCK_FALLBACK

    @property
    def supported_models(self) -> set[str]:
        return {
            self.GROQ_FAST_MODEL,
            self.CEREBRAS_PRIMARY_MODEL,
            self.GROQ_REASONING_MODEL,
        }


settings = Settings()
