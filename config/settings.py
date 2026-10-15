from typing import Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings


load_dotenv()


class Settings(BaseSettings):
    PROJECT_NAME: str = "Enterprise AI Routing Proxy"

    GROQ_API_KEY: Optional[str] = None
    CEREBRAS_API_KEY: Optional[str] = None

    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    CEREBRAS_BASE_URL: str = "https://api.cerebras.ai/v1"
    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"

    REQUEST_TIMEOUT_SECONDS: float = 10.0
    MAX_FALLBACK_RETRIES: int = 3


settings = Settings()
