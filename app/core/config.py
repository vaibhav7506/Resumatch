"""
Central application configuration.

Everything the app needs from the environment is declared here, once,
so no other module reaches into os.environ directly. This is a common
pattern interviewers look for: config as a single validated source of truth.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str
    llm_model: str = "llama-3.3-70b-versatile"

    voyage_api_key: str
    embedding_model: str = "voyage-3"

    database_url: str

    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
