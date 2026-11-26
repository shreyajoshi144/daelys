from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The owner URL (DATABASE_URL) is read only by Alembic; the running app never holds owner credentials.
    app_database_url: str
    daelys_app_password: SecretStr | None = None
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: SecretStr = Field(min_length=32)
    jwt_ttl_minutes: int = Field(default=60, ge=1, le=24 * 60)
    login_rate_limit_per_minute: int = Field(default=10, ge=1)
    cookie_secure: bool = False

    seed_user_password: SecretStr | None = None
    seed_github_installation_id: str | None = None

    github_webhook_secret: SecretStr | None = None
    github_app_id: str | None = None
    github_app_private_key_path: str | None = None
    github_api_url: str = "https://api.github.com"

    anthropic_api_key: SecretStr | None = None
    anthropic_api_base: str = "https://api.anthropic.com"
    llm_routes: str = "anthropic:claude-opus-5-5,anthropic:claude-sonnet-5-5"
    llm_timeout_seconds: float = Field(default=180.0, gt=0)
    llm_rate_limit_per_minute: int = Field(default=20, ge=1)

    voyage_api_key: SecretStr | None = None
    embedding_model: str = "voyage-3.5"

    notiv_path: str | None = None

    smtp_host: str | None = None
    smtp_port: int = 1025
    smtp_from: str = "daelys@localhost"

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
