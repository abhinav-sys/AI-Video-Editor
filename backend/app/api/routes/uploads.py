from __future__ import annotations

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_storage, require_api_key
from app.api.schemas.uploads import UploadedFileInfo, UploadResponse
from app.core.models import Asset, AssetKind, UploadBatch
from app.services.storage import StorageService
from app.services.upload_validation import sniff_is_image, sniff_is_video

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
    # Cap total batch (all videos + assets) to 3× per-file max to prevent abuse
    batch_cap = settings_max * max(3, len(videos) + len(assets))
    batch_total = 0
    upload_id = storage.new_upload_id()
    batch = UploadBatch(id=upload_id)
    db.add(batch)

    video_infos: list[UploadedFileInfo] = []
    asset_infos: list[UploadedFileInfo] = []

    video_dir = storage.upload_dir(upload_id)
    asset_dir = storage.assets_dir(upload_id)

    async def _save(file: UploadFile, dest_dir, kind_hint: str | None = None) -> UploadedFileInfo:
        nonlocal batch_total
        if not file.filename:
            raise HTTPException(status_code=400, detail="File missing filename")
        safe = storage.safe_filename(file.filename)
        classified = kind_hint or storage.classify(safe)
        dest = dest_dir / safe
        size = 0
        header = b""
        async with aiofiles.open(dest, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                if size == 0:
                    header = chunk[:64]
                size += len(chunk)
                batch_total += len(chunk)
                if size > settings_max:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds max upload size ({storage.settings.max_upload_size_mb} MB)",
                    )
                if batch_total > batch_cap:
                    raise HTTPException(
                        status_code=413,
                        detail="Batch upload exceeds total size limit",
                    )
                await out.write(chunk)

        if kind_hint == "video" or classified == "video":
            if not sniff_is_video(header):
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=400,
                    detail=f"File does not look like a video (magic bytes): {safe}",
                )
        elif classified in ("logo", "watermark", "other"):
            # Assets should be images when provided
            if header and not sniff_is_image(header) and not sniff_is_video(header):
                # allow unknown small assets but reject obvious non-media
                if header.startswith(b"MZ") or header.startswith(b"\x7fELF"):
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=400, detail=f"Executable uploads rejected: {safe}")

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
