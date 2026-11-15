"""Runtime configuration, loaded once from environment variables and the repository .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGNISETU_",
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = REPO_ROOT / "data"
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    # NASA FIRMS MAP_KEY. Optional: without it the app uses the keyless yearly archive and 7 day feeds.
    firms_map_key: SecretStr | None = None
    # Shared secret for the ingestion control endpoints. When unset those endpoints are disabled.
    admin_token: SecretStr | None = None

    # Comma separated FIRMS country names that make up the analysis domain.
    countries: str = "India,Pakistan,Argentina"

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    allowed_origins: str = ""
    enable_hsts: bool = False
    trust_forwarded_for: bool = False

    rate_limit_per_minute: int = Field(default=300, ge=10, le=100_000)
    heavy_rate_limit_per_minute: int = Field(default=180, ge=1, le=10_000)
    max_body_bytes: int = Field(default=512 * 1024, ge=1024, le=8 * 1024 * 1024)
    max_aoi_vertices: int = Field(default=20_000, ge=4, le=200_000)
    max_aoi_area_km2: float = Field(default=6_000_000.0, gt=0)
    min_aoi_area_km2: float = Field(default=25.0, gt=0)
    max_map_points: int = Field(default=40_000, ge=1000, le=200_000)
    max_concurrent_heavy: int = Field(default=3, ge=1, le=32)
    aoi_cache_entries: int = Field(default=48, ge=1, le=1024)

    # Minutes between automatic near real time refreshes while the server runs. 0 disables the scheduler.
    nrt_refresh_minutes: int = Field(default=180, ge=0, le=24 * 60)
    log_level: str = "INFO"

    @field_validator("firms_map_key", "admin_token", mode="before")
    @classmethod
    def _blank_secret_is_unset(cls, value: object) -> object:
        """An empty line in .env (KEY=) means the secret is not configured, not an empty secret."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("admin_token")
    @classmethod
    def _admin_token_strength(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 16:
            raise ValueError("admin_token must be at least 16 characters long")
        return value

    @field_validator("log_level")
    @classmethod
    def _log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("log_level must be DEBUG, INFO, WARNING or ERROR")
        return level

    @property
    def country_list(self) -> list[str]:
        return [c.strip() for c in self.countries.split(",") if c.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def allowed_origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def store_dir(self) -> Path:
        return self.data_dir / "store"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    for path in (settings.store_dir, settings.cache_dir, settings.tmp_dir):
        path.mkdir(parents=True, exist_ok=True)
    return settings
