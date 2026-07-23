from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    database_url: str = "sqlite:///./app.db"
    storage_root: str = "./storage"
    max_concurrent_renders: int = 2
    max_upload_size_mb: int = 500
    cors_origins: str = "http://localhost:3000"

    api_key: str = "dev-api-key-change-me"
    auth_enabled: bool = True
    rate_limit_per_minute: int = 60

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5"
    llm_provider: str = "ollama"

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ffmpeg_fontfile: str = ""  # optional absolute path; helps drawtext on Windows

    creatomate_api_key: str = ""
    creatomate_default_template_id: str = ""
    creatomate_api_base: str = "https://api.creatomate.com/v1"
    creatomate_poll_interval_sec: float = 2.0
    creatomate_poll_timeout_sec: float = 300.0
    # Optional public origin (ngrok/cloudflare) so Creatomate can fetch local uploads
    public_base_url: str = ""

    worker_poll_interval_sec: float = 1.0
    soft_retry_count: int = 1

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_root).resolve()

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
