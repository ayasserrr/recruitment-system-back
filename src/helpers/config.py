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

    # Application base URL — used to build candidate application links
    APP_BASE_URL: str = "https://yourdomain.com"

    # SMTP — email notifications
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_NAME: str = "Recruitment System"

    # Celery / Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # LiveKit — voice interview infrastructure
    LIVEKIT_URL: str = "wss://ai-interview-lgcdo8l6.livekit.cloud"
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""
    LIVEKIT_WEBHOOK_SECRET: str = ""   # same as API_SECRET for signature validation
    LIVEKIT_AGENT_NAME: str = "interview-agent"
    LIVEKIT_HR_AGENT_NAME: str = "interview-agent"  # override in .env to use a separate HR agent worker
    PORT: int = 8787

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


def get_settings():
    return settings()