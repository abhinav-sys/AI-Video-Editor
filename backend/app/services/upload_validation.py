"""Upload content validation helpers (magic bytes + batch caps)."""

from __future__ import annotations

# ISO BMFF / MP4 / MOV / common containers
_VIDEO_MAGIC = (
    (b"\x00\x00\x00", b"ftyp"),  # ****ftyp at offset 4 — checked specially
    (b"\x1a\x45\xdf\xa3", None),  # Matroska / WebM
    (b"RIFF", b"AVI "),  # AVI
)

_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", None),  # JPEG
    (b"\x89PNG\r\n\x1a\n", None),  # PNG
    (b"GIF87a", None),
    (b"GIF89a", None),
    (b"RIFF", b"WEBP"),
)


def sniff_is_image(header: bytes) -> bool:
    if len(header) < 3:
        return False
    for prefix, mid in _IMAGE_MAGIC:
        if header.startswith(prefix):
            if mid is None:
                return True
            if len(header) >= 12 and mid in header[:16]:
                return True
    return False


def sniff_is_video(header: bytes) -> bool:
    if len(header) < 8:
        return False
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return True
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return True
    if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"AVI ":
        return True
    # MPEG-PS / elementary — weak
    if header[:3] == b"\x00\x00\x01":
        return True
    return False
