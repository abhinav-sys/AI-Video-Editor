"""Typed FFmpeg filter-graph fragments (Phase 7 hardening)."""

from __future__ import annotations

from dataclasses import dataclass, field


def enable_between(t_start: float | None, t_end: float | None) -> str:
    """Return `:enable='between(t,start,end)'` or empty if unbounded."""
    if t_start is None and t_end is None:
        return ""
    start = 0.0 if t_start is None else max(0.0, float(t_start))
    end = 1e9 if t_end is None else max(start + 0.01, float(t_end))
    return f":enable='between(t,{start:.3f},{end:.3f})'"


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
        .replace("&", "\\&")
    )


def escape_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:")


@dataclass
class FilterGraph:
    """Accumulate labeled filter chains without raw f-string sprawl."""

    filters: list[str] = field(default_factory=list)
    current: str = "[0:v]"
    _n: int = 0

    def _label(self, prefix: str) -> str:
        self._n += 1
        return f"[{prefix}{self._n}]"

    def drawtext(
        self,
        *,
        text: str,
        fontsize: int,
        fontcolor: str,
        x: str,
        y: str | int,
        font_opt: str = "",
        t_start: float | None = None,
        t_end: float | None = None,
    ) -> str:
        label = self._label("t")
        en = enable_between(t_start, t_end)
        self.filters.append(
            f"{self.current}drawtext=text='{escape_drawtext(text)}':fontsize={fontsize}:"
            f"fontcolor={fontcolor}:x={x}:y={y}{font_opt}{en}{label}"
        )
        self.current = label
        return label

    def overlay(
        self,
        input_ref: str,
        *,
        x: int | str,
        y: int | str,
        fmt: str = "auto",
        t_start: float | None = None,
        t_end: float | None = None,
        prefix: str = "ov",
    ) -> str:
        label = self._label(prefix)
        en = enable_between(t_start, t_end)
        self.filters.append(
            f"{self.current}{input_ref}overlay={x}:{y}:format={fmt}{en}{label}"
        )
        self.current = label
        return label

    def removelogo(self, mask_path: str) -> str:
        label = self._label("rl")
        mask_esc = escape_path(mask_path)
        self.filters.append(f"{self.current}removelogo=filename='{mask_esc}'{label}")
        self.current = label
        return label

    def build(self) -> str:
        return ";".join(self.filters)
