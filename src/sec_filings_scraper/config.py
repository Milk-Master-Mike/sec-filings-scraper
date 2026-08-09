from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEC_", env_file=".env.local", extra="ignore"
    )

    user_agent: str | None = None
    cache_dir: Path = Path(".cache/sec-filings-scraper")
    cache_ttl_seconds: int = Field(default=3600, ge=60)
    max_concurrency: int = Field(default=2, ge=1, le=4)
    requests_per_second: float = Field(default=8.0, gt=0, le=10)
    timeout_seconds: float = Field(default=20.0, gt=0, le=60)

    def validated_user_agent(self) -> str:
        value = (self.user_agent or "").strip()
        if not value or "@" not in value or len(value) < 12:
            raise ValueError(
                "Live SEC collection requires SEC_USER_AGENT with an application "
                "name and monitored email address (for example: app/1.0 you@example.com)."
            )
        return value

