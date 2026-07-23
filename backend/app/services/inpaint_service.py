"""Background reconstruction: flat-fill fast path + optional LaMa / Telea."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from app.config import get_settings
from app.core.logging import get_logger
from app.services.text_heal import build_glyph_mask, heal_crop
from app.services.text_locate import _is_banner_pixel

logger = get_logger(__name__)


@dataclass
class InpaintResult:
    mode: str  # flat | banner | telea | lama | inpaint
    image: Image.Image


def classify_region_mode(crop: Image.Image) -> str:
    """Return flat when banner/plate; else generative path."""
    arr = np.asarray(crop.convert("RGB"))
    h, w = arr.shape[:2]
    if h < 2 or w < 2:
        return "flat"
    border = np.concatenate(
        [
            arr[0, :, :].reshape(-1, 3),
            arr[-1, :, :].reshape(-1, 3),
            arr[:, 0, :].reshape(-1, 3),
            arr[:, -1, :].reshape(-1, 3),
        ],
        axis=0,
    )
    bannerish = sum(1 for px in border if _is_banner_pixel(tuple(int(c) for c in px)))
    if bannerish / max(1, len(border)) > 0.45:
        return "flat"
    return "lama" if get_settings().enable_lama_inpaint else "telea"


def inpaint_region(crop: Image.Image, *, force_mode: str | None = None) -> InpaintResult:
    """Heal a crop: flat/banner via heal_crop, Telea, or LaMa when enabled."""
    mask = build_glyph_mask(crop)
    mode = force_mode or classify_region_mode(crop)

    if mode in ("flat", "banner"):
        healed, used = heal_crop(crop, mask, force_mode="flat" if mode == "flat" else "banner")
        return InpaintResult(mode=used, image=healed)

    if mode == "lama" and get_settings().enable_lama_inpaint:
        try:
            rgb = np.asarray(crop.convert("RGB"))
            mask_u8 = np.asarray(mask)
            if mask_u8.ndim == 3:
                mask_u8 = mask_u8[:, :, 0]
            mask_bin = (mask_u8 > 0).astype(np.uint8) * 255
            out = _lama_inpaint(rgb, mask_bin)
            return InpaintResult(mode="lama", image=Image.fromarray(out))
        except Exception as exc:
            logger.warning("LaMa failed (%s); falling back to Telea/heal", exc)

    # Prefer existing heal_crop inpaint (Telea under the hood) for non-flat
    try:
        healed, used = heal_crop(crop, mask, force_mode="inpaint")
        return InpaintResult(mode="telea" if used == "inpaint" else used, image=healed)
    except Exception as exc:
        logger.warning("Inpaint failed (%s); flat heal", exc)
        healed, used = heal_crop(crop, mask, force_mode="flat")
        return InpaintResult(mode=used, image=healed)


def _lama_inpaint(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Attempt simple-lama-inpainting; raise if unavailable."""
    try:
        from simple_lama_inpainting import SimpleLama  # type: ignore

        lama = SimpleLama()
        pil = Image.fromarray(rgb)
        mask_pil = Image.fromarray(mask)
        result = lama(pil, mask_pil)
        return np.asarray(result.convert("RGB"))
    except ImportError as exc:
        raise RuntimeError("simple-lama-inpainting not installed") from exc
