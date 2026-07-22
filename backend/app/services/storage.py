from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.config import Settings, get_settings


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


class StorageService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.root = self.settings.storage_path
        self.uploads = self.root / "uploads"
        self.assets = self.root / "assets"
        self.outputs = self.root / "outputs"
        self.zips = self.root / "zips"

    def ensure_dirs(self) -> None:
        for path in (self.uploads, self.assets, self.outputs, self.zips):
            path.mkdir(parents=True, exist_ok=True)

    def new_upload_id(self) -> str:
        return str(uuid.uuid4())

    def upload_dir(self, upload_id: str) -> Path:
        path = self.uploads / upload_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def assets_dir(self, upload_id: str) -> Path:
        path = self.assets / upload_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def output_dir(self, job_id: str) -> Path:
        path = self.outputs / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def zip_path(self, job_id: str) -> Path:
        self.zips.mkdir(parents=True, exist_ok=True)
        return self.zips / f"{job_id}.zip"

    @staticmethod
    def classify(filename: str) -> str:
        ext = Path(filename).suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            return "video"
        if ext in IMAGE_EXTENSIONS:
            name = filename.lower()
            if "logo" in name:
                return "logo"
            if "watermark" in name or "wm" in name:
                return "watermark"
            return "other"
        return "other"

    @staticmethod
    def safe_filename(filename: str) -> str:
        name = Path(filename).name
        return "".join(c if c.isalnum() or c in "._- " else "_" for c in name) or "file"

    def resolve_asset(self, upload_id: str, filename: str) -> Path | None:
        safe = self.safe_filename(filename)
        candidate = self.assets_dir(upload_id) / safe
        if candidate.is_file():
            return candidate
        # also check original basename match in assets dir
        for path in self.assets_dir(upload_id).iterdir():
            if path.name.lower() == filename.lower() or path.name.lower() == safe.lower():
                return path
        return None

    def cleanup_upload(self, upload_id: str) -> None:
        for base in (self.uploads, self.assets):
            path = base / upload_id
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
