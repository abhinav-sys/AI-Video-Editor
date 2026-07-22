from __future__ import annotations

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_storage, require_api_key
from app.api.schemas.uploads import UploadedFileInfo, UploadResponse
from app.core.models import Asset, AssetKind, UploadBatch
from app.services.storage import StorageService

router = APIRouter(prefix="/uploads", tags=["uploads"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=UploadResponse)
async def create_upload(
    videos: list[UploadFile] = File(default=[]),
    assets: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    storage: StorageService = Depends(get_storage),
) -> UploadResponse:
    if not videos:
        raise HTTPException(status_code=400, detail="At least one video is required")

    settings_max = storage.settings.max_upload_bytes
    upload_id = storage.new_upload_id()
    batch = UploadBatch(id=upload_id)
    db.add(batch)

    video_infos: list[UploadedFileInfo] = []
    asset_infos: list[UploadedFileInfo] = []

    video_dir = storage.upload_dir(upload_id)
    asset_dir = storage.assets_dir(upload_id)

    async def _save(file: UploadFile, dest_dir, kind_hint: str | None = None) -> UploadedFileInfo:
        if not file.filename:
            raise HTTPException(status_code=400, detail="File missing filename")
        safe = storage.safe_filename(file.filename)
        classified = kind_hint or storage.classify(safe)
        if classified == "video" and kind_hint != "video":
            # when uploaded via assets field but is video — still treat by extension
            pass
        dest = dest_dir / safe
        size = 0
        async with aiofiles.open(dest, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings_max:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds max upload size ({storage.settings.max_upload_size_mb} MB)",
                    )
                await out.write(chunk)

        kind_enum = {
            "video": AssetKind.video,
            "logo": AssetKind.logo,
            "watermark": AssetKind.watermark,
            "other": AssetKind.other,
        }.get(classified, AssetKind.other)

        if kind_hint == "video":
            kind_enum = AssetKind.video

        db.add(
            Asset(
                upload_id=upload_id,
                filename=safe,
                path=str(dest),
                kind=kind_enum,
            )
        )
        return UploadedFileInfo(filename=safe, kind=kind_enum.value, size_bytes=size)

    for vf in videos:
        info = await _save(vf, video_dir, kind_hint="video")
        video_infos.append(info)

    for af in assets:
        info = await _save(af, asset_dir)
        asset_infos.append(info)

    db.commit()
    return UploadResponse(upload_id=upload_id, videos=video_infos, assets=asset_infos)
