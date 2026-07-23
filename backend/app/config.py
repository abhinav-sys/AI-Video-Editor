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
    # Local default remains SQLite; docker-compose.prod / Phase 1 uses Postgres.
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
    fonts_dir: str = ""  # bundled fonts dir (Phase 7); preferred over Windows paths

    creatomate_api_key: str = ""
    creatomate_default_template_id: str = ""
    creatomate_api_base: str = "https://api.creatomate.com/v1"
    creatomate_poll_interval_sec: float = 2.0
    creatomate_poll_timeout_sec: float = 300.0
    # Optional public origin (ngrok/cloudflare) so Creatomate can fetch local uploads
    public_base_url: str = ""

    worker_poll_interval_sec: float = 1.0
    soft_retry_count: int = 1

    # Phase 1: durable queue. Inline worker kept for local SQLite/dev convenience.
    use_inline_worker: bool = True
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    job_lease_seconds: int = 120
    job_reconcile_interval_sec: float = 30.0
    ocr_provider: str = "easyocr"  # easyocr | paddle
    # Run EasyOCR in a child process so native crashes (0xC0000005) don't kill uvicorn.
    ocr_subprocess: bool = True
    ocr_subprocess_timeout_sec: float = 180.0
    enable_lama_inpaint: bool = False
    enable_vision_detect: bool = True
    max_shots_sampled: int = 12
    hwaccel: str = ""  # empty | nvenc | qsv
    # Fast local testing: trim source to first N seconds (0 = off).
    test_clip_seconds: float = 0.0
    # Stop after EditableTemplate + preview stills (skip video inpaint + final encode).
    template_only: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_root).resolve()

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url.strip() or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend.strip() or self.redis_url

    @property
    def resolved_fonts_dir(self) -> Path:
        if self.fonts_dir.strip():
            return Path(self.fonts_dir).resolve()
        # Prefer bundled assets next to the package /app/assets/fonts in Docker
        candidates = [
            Path(__file__).resolve().parent.parent / "assets" / "fonts",
            Path("/app/assets/fonts"),
        ]
        for c in candidates:
            if c.is_dir():
                return c
        return candidates[0]


@lru_cache
def get_settings() -> Settings:
    return Settings()
