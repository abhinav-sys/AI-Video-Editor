"""OCR provider interface (EasyOCR default, optional PaddleOCR)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.core.logging import get_logger
from app.services.text_ocr import OcrBox

logger = get_logger(__name__)


class OcrProvider(ABC):
    @abstractmethod
    def read_boxes(self, image_path: Path) -> list[OcrBox]:
        ...


class EasyOcrProvider(OcrProvider):
    def read_boxes(self, image_path: Path) -> list[OcrBox]:
        from app.services.text_ocr import read_ocr_boxes

        return read_ocr_boxes(image_path)


class PaddleOcrProvider(OcrProvider):
    def __init__(self) -> None:
        self._ocr = None

    def _ensure(self):
        if self._ocr is not None:
            return self._ocr
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCR not installed. pip install paddlepaddle paddleocr "
                "or set OCR_PROVIDER=easyocr"
            ) from exc
        self._ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        return self._ocr

    def read_boxes(self, image_path: Path) -> list[OcrBox]:
        ocr = self._ensure()
        result = ocr.ocr(str(image_path), cls=True)
        boxes: list[OcrBox] = []
        if not result:
            return boxes
        for page in result:
            if not page:
                continue
            for line in page:
                pts, (text, conf) = line[0], line[1]
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                x, y = int(min(xs)), int(min(ys))
                w, h = int(max(xs) - x), int(max(ys) - y)
                boxes.append(
                    OcrBox(
                        text=str(text),
                        x=x,
                        y=y,
                        w=max(1, w),
                        h=max(1, h),
                        confidence=float(conf),
                    )
                )
        return boxes


@lru_cache
def get_ocr_provider() -> OcrProvider:
    name = (get_settings().ocr_provider or "easyocr").strip().lower()
    if name == "paddle":
        try:
            return PaddleOcrProvider()
        except Exception as exc:
            logger.warning("PaddleOCR unavailable (%s); falling back to EasyOCR", exc)
    return EasyOcrProvider()
