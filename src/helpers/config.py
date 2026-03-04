from pydantic_settings import BaseSettings, SettingsConfigDict


class settings(BaseSettings):
    APP_NAME: str
    APP_VERSION: str

    FILE_ALLOWED_TYPES: list[str]
    FILE_MAX_SIZE: int
    FILE_DEFAULT_CHUNK_SIZE: int

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.1"
    OLLAMA_TIMEOUT_SEC: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
    )


def get_settings():
    return settings()