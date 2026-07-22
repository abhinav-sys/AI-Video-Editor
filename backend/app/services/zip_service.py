from __future__ import annotations

import zipfile
from pathlib import Path

from app.core.logging import get_logger
from app.services.storage import StorageService

logger = get_logger(__name__)


class ZipService:
    def __init__(self, storage: StorageService | None = None) -> None:
        self.storage = storage or StorageService()

    def build_job_zip(self, job_id: str, output_files: list[Path]) -> Path:
        zip_path = self.storage.zip_path(job_id)
        if zip_path.exists():
            zip_path.unlink()

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in output_files:
                if file_path.is_file():
                    zf.write(file_path, arcname=file_path.name)
                    logger.info("Added %s to zip for job %s", file_path.name, job_id)

        return zip_path
