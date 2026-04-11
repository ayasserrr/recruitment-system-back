from pydantic_settings import BaseSettings, SettingsConfigDict


class settings(BaseSettings):
    APP_NAME: str
    APP_VERSION: str

    DATABASE_URL: str = "postgresql://postgres:12345@localhost:5432/recruitment_system_db"

    FILE_ALLOWED_TYPES: list[str]
    FILE_MAX_SIZE: int
    FILE_DEFAULT_CHUNK_SIZE: int

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.1"
    OLLAMA_TIMEOUT_SEC: int = 60

    OPENAI_API_KEY: str = ""

    LINKEDIN_CLIENT_ID: str = ""
    LINKEDIN_CLIENT_SECRET: str = ""
    LINKEDIN_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/linkedin/callback"

    # Celery / Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


def get_settings():
    return settings()