from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

TERMINAL_OK = {"succeeded"}
TERMINAL_FAIL = {"failed"}


class CreatomateError(RuntimeError):
    pass


class CreatomateService:
    """Thin client for Creatomate REST API (template + RenderScript source renders)."""

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def configured(self) -> bool:
        return bool(self.settings.creatomate_api_key.strip())

    def _headers(self) -> dict[str, str]:
        key = self.settings.creatomate_api_key.strip()
        if not key:
            raise CreatomateError("CREATOMATE_API_KEY is not configured")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        base = self.settings.creatomate_api_base.rstrip("/")
        return f"{base}/{path.lstrip('/')}"

    async def health_check(self) -> bool:
        if not self.configured:
            return False
        try:
            await self.list_templates()
            return True
        except Exception as exc:
            logger.warning("Creatomate health check failed: %s", exc)
            return False

    async def list_templates(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.get(self._url("/templates"), headers=self._headers())
            if res.status_code >= 400:
                raise CreatomateError(f"List templates failed ({res.status_code}): {res.text}")
            data = res.json()
            return data if isinstance(data, list) else []

    async def get_template(self, template_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.get(
                self._url(f"/templates/{template_id}"),
                headers=self._headers(),
            )
            if res.status_code >= 400:
                raise CreatomateError(f"Get template failed ({res.status_code}): {res.text}")
            return res.json()

    async def create_render(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                self._url("/renders"),
                headers=self._headers(),
                json=payload,
            )
            if res.status_code >= 400:
                raise CreatomateError(f"Create render failed ({res.status_code}): {res.text}")
            data = res.json()
            # API returns a single object or a one-item array
            if isinstance(data, list):
                if not data:
                    raise CreatomateError("Create render returned an empty list")
                return data[0]
            return data

    async def get_render(self, render_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.get(
                self._url(f"/renders/{render_id}"),
                headers=self._headers(),
            )
            if res.status_code >= 400:
                raise CreatomateError(f"Get render failed ({res.status_code}): {res.text}")
            return res.json()

    async def wait_for_render(
        self,
        render_id: str,
        *,
        on_progress: Any | None = None,
    ) -> dict[str, Any]:
        deadline = asyncio.get_event_loop().time() + self.settings.creatomate_poll_timeout_sec
        elapsed_ticks = 0
        while True:
            render = await self.get_render(render_id)
            status = str(render.get("status") or "").lower()
            if on_progress:
                # Map planned/waiting/rendering → rough progress
                pct = 15.0
                if status in {"waiting", "transcribing"}:
                    pct = 35.0
                elif status == "rendering":
                    pct = min(90.0, 45.0 + elapsed_ticks * 3.0)
                elif status in TERMINAL_OK:
                    pct = 100.0
                await on_progress(pct)
            if status in TERMINAL_OK:
                return render
            if status in TERMINAL_FAIL:
                msg = render.get("error_message") or render.get("error") or "Creatomate render failed"
                raise CreatomateError(str(msg))
            if asyncio.get_event_loop().time() >= deadline:
                raise CreatomateError(f"Creatomate render timed out (id={render_id})")
            elapsed_ticks += 1
            await asyncio.sleep(self.settings.creatomate_poll_interval_sec)

    async def download_file(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            res = await client.get(url)
            if res.status_code >= 400:
                raise CreatomateError(f"Download failed ({res.status_code}): {url}")
            dest.write_bytes(res.content)
        return dest

    async def upload_temp_public(self, path: Path) -> str:
        """Host a local file briefly so Creatomate can fetch it as a source URL."""
        errors: list[str] = []
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            hosts = [
                (
                    "catbox",
                    "https://catbox.moe/user/api.php",
                    {"data": {"reqtype": "fileupload"}, "file_field": "fileToUpload"},
                ),
                (
                    "litterbox",
                    "https://litterbox.catbox.moe/resources/internals/api.php",
                    {
                        "data": {"reqtype": "fileupload", "time": "24h"},
                        "file_field": "fileToUpload",
                    },
                ),
                (
                    "0x0",
                    "https://0x0.st",
                    {"data": None, "file_field": "file"},
                ),
                (
                    "tmpfiles",
                    "https://tmpfiles.org/api/v1/upload",
                    {"data": None, "file_field": "file", "json": True},
                ),
            ]
            for name, url, opts in hosts:
                try:
                    with path.open("rb") as fh:
                        files = {opts["file_field"]: (path.name, fh)}
                        data = opts.get("data")
                        res = await client.post(url, data=data, files=files)
                    body = (res.text or "").strip()
                    if opts.get("json") and res.status_code < 400:
                        payload = res.json()
                        page_url = (payload.get("data") or {}).get("url")
                        if page_url and "tmpfiles.org/" in page_url:
                            published = page_url.replace(
                                "tmpfiles.org/", "tmpfiles.org/dl/", 1
                            )
                            logger.info("Published %s -> %s (%s)", path.name, published, name)
                            return published
                        errors.append(f"{name} {res.status_code}: {body[:120]}")
                        continue
                    if res.status_code < 400 and body.startswith("http"):
                        published = body.split()[0]
                        logger.info("Published %s -> %s (%s)", path.name, published, name)
                        return published
                    errors.append(f"{name} {res.status_code}: {body[:120]}")
                except Exception as exc:
                    errors.append(f"{name}: {exc}")

            # gofile.io upload works, but guest uploads lack a Creatomate-usable direct link.
            # Prefer PUBLIC_BASE_URL (ngrok/cloudflare/localtunnel) for reliable fetches.
            try:
                server_res = await client.get("https://api.gofile.io/servers")
                server = "store1"
                if server_res.status_code < 400:
                    servers = (server_res.json().get("data") or {}).get("servers") or []
                    if servers:
                        server = servers[0].get("name") or server
                with path.open("rb") as fh:
                    res = await client.post(
                        f"https://{server}.gofile.io/uploadFile",
                        files={"file": (path.name, fh)},
                    )
                if res.status_code < 400:
                    data = res.json().get("data") or {}
                    published = data.get("directLink")
                    if published and str(published).startswith("http"):
                        logger.info("Published %s -> %s (gofile)", path.name, published)
                        return str(published)
                    # Guest upload: no directLink — keep error informative
                    errors.append(
                        "gofile: uploaded but no directLink (needs PUBLIC_BASE_URL or premium)"
                    )
                else:
                    errors.append(f"gofile {res.status_code}: {(res.text or '')[:120]}")
            except Exception as exc:
                errors.append(f"gofile: {exc}")

        raise CreatomateError(
            "Could not publish upload for Creatomate (all temp hosts failed). "
            + " | ".join(errors)
            + ". Tip: set PUBLIC_BASE_URL to an ngrok/cloudflare tunnel to your API."
        )

    async def make_hostable_copy(self, input_path: Path, ffmpeg_path: str) -> Path:
        """Re-encode a lean same-resolution copy so temp hosts accept large WhatsApp clips."""
        import tempfile

        dest = Path(tempfile.mkdtemp(prefix="vgai_cm_")) / f"{input_path.stem}_lean.mp4"
        cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            str(input_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            str(dest),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not dest.is_file():
            raise CreatomateError(
                f"Failed to prepare hostable video copy: {stderr.decode(errors='ignore')[-400:]}"
            )
        logger.info(
            "Hostable copy %s -> %s (%d bytes)",
            input_path.name,
            dest.name,
            dest.stat().st_size,
        )
        return dest

    def build_template_payload(
        self,
        *,
        template_id: str,
        modifications: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "modifications": modifications,
        }

    def build_source_payload(
        self,
        *,
        text_primary: str,
        text_secondary: str,
        video_url: str | None = None,
        width: int = 1080,
        height: int = 1080,
        duration: float = 6.0,
    ) -> dict[str, Any]:
        elements: list[dict[str, Any]] = []
        if video_url:
            elements.append(
                {
                    "type": "video",
                    "source": video_url,
                    "track": 1,
                    "width": "100%",
                    "height": "100%",
                    "fit": "cover",
                }
            )
        else:
            elements.append(
                {
                    "type": "shape",
                    "track": 1,
                    "width": "100%",
                    "height": "100%",
                    "fill_color": "#111111",
                    "path": "M 0 0 L 100 0 L 100 100 L 0 100 L 0 0 Z",
                }
            )
        elements.extend(
            [
                {
                    "type": "text",
                    "name": "Text-1",
                    "text": text_primary,
                    "track": 2,
                    "y": "38%",
                    "width": "86%",
                    "height": "22%",
                    "fill_color": "#ffffff",
                    "font_family": "Montserrat",
                    "font_weight": "800",
                    "x_alignment": "50%",
                    "y_alignment": "50%",
                },
                {
                    "type": "text",
                    "name": "Text-2",
                    "text": text_secondary,
                    "track": 3,
                    "y": "62%",
                    "width": "80%",
                    "height": "14%",
                    "fill_color": "#ffffff",
                    "font_family": "Montserrat",
                    "font_weight": "600",
                    "x_alignment": "50%",
                    "y_alignment": "50%",
                },
            ]
        )
        return {
            "source": {
                "output_format": "mp4",
                "width": width,
                "height": height,
                "duration": duration,
                "elements": elements,
            }
        }

    @staticmethod
    def _rgb_css(rgb: tuple[int, int, int]) -> str:
        r, g, b = rgb
        return f"#{r:02x}{g:02x}{b:02x}"

    def build_ocr_edit_source(
        self,
        *,
        video_url: str,
        width: int,
        height: int,
        duration: float,
        regions: list[Any],
        logo_url: str | None = None,
        watermark_url: str | None = None,
        watermark_position: str | None = None,
    ) -> dict[str, Any]:
        """Build a Creatomate RenderScript template from OCR paint regions.

        Mirrors Bulkcut: full-frame source video + cover shapes + replacement text.
        """
        dur = max(float(duration or 1.0), 0.5)
        w = max(int(width), 1)
        h = max(int(height), 1)
        elements: list[dict[str, Any]] = [
            {
                "type": "video",
                "name": "Source-Video",
                "source": video_url,
                "track": 1,
                "time": 0,
                "duration": dur,
                "width": "100%",
                "height": "100%",
                "fit": "fill",
            }
        ]
        track = 2
        for i, region in enumerate(regions):
            x_pct = (region.x / w) * 100
            y_pct = (region.y / h) * 100
            w_pct = (region.w / w) * 100
            h_pct = (region.h / h) * 100
            fill = self._rgb_css(region.fill_rgb)
            font = self._rgb_css(region.font_rgb)
            text_y = region.text_y if getattr(region, "text_y", 0) else (
                region.y + max(0, (region.h - region.fontsize) // 2)
            )
            text_y_pct = (text_y / h) * 100
            fontsize_pct = max((region.fontsize / h) * 100, 1.2)

            elements.append(
                {
                    "type": "shape",
                    "name": f"Cover-{i}",
                    "track": track,
                    "time": 0,
                    "duration": dur,
                    "x": f"{x_pct:.4f}%",
                    "y": f"{y_pct:.4f}%",
                    "width": f"{w_pct:.4f}%",
                    "height": f"{h_pct:.4f}%",
                    "x_anchor": "0%",
                    "y_anchor": "0%",
                    "fill_color": fill,
                    "path": "M 0 0 L 100 0 L 100 100 L 0 100 L 0 0 Z",
                }
            )
            track += 1
            elements.append(
                {
                    "type": "text",
                    "name": f"Replace-{i}",
                    "track": track,
                    "time": 0,
                    "duration": dur,
                    "text": region.text,
                    "x": f"{x_pct:.4f}%",
                    "y": f"{text_y_pct:.4f}%",
                    "width": f"{max(w_pct, 2):.4f}%",
                    "height": f"{max(h_pct, fontsize_pct):.4f}%",
                    "x_anchor": "0%",
                    "y_anchor": "0%",
                    "x_alignment": "50%" if region.align == "center" else "0%",
                    "y_alignment": "50%",
                    "fill_color": font,
                    "font_family": "Montserrat",
                    "font_weight": "800" if getattr(region, "bold", False) else "600",
                    "font_size": f"{fontsize_pct:.3f}vh",
                    "text_wrap": False,
                }
            )
            track += 1

        if logo_url:
            elements.append(
                {
                    "type": "image",
                    "name": "Logo",
                    "source": logo_url,
                    "track": track,
                    "time": 0,
                    "duration": dur,
                    "x": "3%",
                    "y": "3%",
                    "width": "15%",
                    "height": "12%",
                    "x_anchor": "0%",
                    "y_anchor": "0%",
                    "fit": "contain",
                }
            )
            track += 1

        if watermark_url or watermark_position:
            pos = (watermark_position or "bottom-right").replace("_", "-")
            coords = {
                "top-left": ("3%", "3%", "0%", "0%"),
                "top-right": ("97%", "3%", "100%", "0%"),
                "bottom-left": ("3%", "97%", "0%", "100%"),
                "bottom-right": ("97%", "97%", "100%", "100%"),
                "center": ("50%", "50%", "50%", "50%"),
            }.get(pos, ("97%", "97%", "100%", "100%"))
            x, y, xa, ya = coords
            if watermark_url:
                elements.append(
                    {
                        "type": "image",
                        "name": "Watermark",
                        "source": watermark_url,
                        "track": track,
                        "time": 0,
                        "duration": dur,
                        "x": x,
                        "y": y,
                        "width": "12%",
                        "height": "10%",
                        "x_anchor": xa,
                        "y_anchor": ya,
                        "fit": "contain",
                        "opacity": "70%",
                    }
                )
            else:
                elements.append(
                    {
                        "type": "text",
                        "name": "Watermark",
                        "text": "WATERMARK",
                        "track": track,
                        "time": 0,
                        "duration": dur,
                        "x": x,
                        "y": y,
                        "x_anchor": xa,
                        "y_anchor": ya,
                        "fill_color": "#ffffff",
                        "opacity": "60%",
                        "font_family": "Montserrat",
                        "font_weight": "700",
                        "font_size": "3vh",
                    }
                )

        return {
            "source": {
                "output_format": "mp4",
                "width": w,
                "height": h,
                "duration": dur,
                "elements": elements,
            }
        }
