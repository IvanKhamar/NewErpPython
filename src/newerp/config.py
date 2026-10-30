"""App settings from env vars `NEWERP_*` and the git-ignored `.env` (like appsettings.json + user-secrets)."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEWERP_", env_file=".env", extra="ignore", env_nested_delimiter="__")

    # Development, Testing or Production (like ASPNETCORE_ENVIRONMENT).
    environment: str = "Development"

    db_host: str = "localhost"
    db_port: int = 3307
    db_name: str = "newerp_dev"
    db_user: str = "newerp"
    db_password: SecretStr = SecretStr("")

    redis_url: str = "redis://localhost:6379/0"

    # Key that signs the auth cookie. Without it a random key is made at start, so sessions end on restart.
    auth_secret: SecretStr | None = None

    # Language of the OpenCart lookups (dd_order_status etc.). The real database has every name with language_id 3.
    language_id: int = 3

    @property
    def is_development(self) -> bool:
        return self.environment in ("Development", "Testing")

    @property
    def database_url(self) -> URL:
        return URL.create(
            "mysql+aiomysql",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
