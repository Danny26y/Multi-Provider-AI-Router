from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings


load_dotenv()


class Settings(BaseSettings):
    PROJECT_NAME: str = "Enterprise AI Routing Proxy"
    APP_ENV: Literal["development", "testing", "production"] = "production"

    ROUTER_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    CEREBRAS_API_KEY: Optional[str] = None

    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    CEREBRAS_BASE_URL: str = "https://api.cerebras.ai/v1"

    REQUEST_TIMEOUT_SECONDS: float = 10.0
    ENABLE_MOCK_FALLBACK: bool = False

    @property
    def mock_fallback_enabled(self) -> bool:
        return self.APP_ENV in {"development", "testing"} and self.ENABLE_MOCK_FALLBACK


settings = Settings()
