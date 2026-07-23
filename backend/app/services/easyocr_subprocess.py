"""Client for the EasyOCR worker subprocess (see easyocr_worker.py)."""

from __future__ import annotations

import atexit
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_WORKER_MODULE = "app.services.easyocr_worker"


class EasyOcrSubprocessError(RuntimeError):
    pass


class EasyOcrSubprocessClient:
    """Singleton-ish client that keeps one EasyOCR child alive."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None

    def close(self) -> None:
        with self._lock:
            self._kill_unlocked()

    def readtext(self, image_path: Path) -> list:
        path = Path(image_path).resolve()
        if not path.is_file():
            return []

        timeout = float(get_settings().ocr_subprocess_timeout_sec)
        with self._lock:
            last_err: Exception | None = None
            for attempt in range(2):
                try:
                    self._ensure_unlocked()
                    return self._call_unlocked(path, timeout=timeout)
                except EasyOcrSubprocessError as exc:
                    last_err = exc
                    logger.warning(
                        "EasyOCR subprocess failed (attempt %d): %s — restarting worker",
                        attempt + 1,
                        exc,
                    )
                    self._kill_unlocked()
            raise EasyOcrSubprocessError(
                f"EasyOCR subprocess failed after restart: {last_err}"
            )

    def _ensure_unlocked(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._kill_unlocked()
        logger.info("Starting EasyOCR subprocess worker")
        self._proc = subprocess.Popen(
            [sys.executable, "-u", "-m", _WORKER_MODULE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        assert self._proc.stdin and self._proc.stdout
        # Wait for ready line (model load can take a while).
        ready_timeout = max(60.0, float(get_settings().ocr_subprocess_timeout_sec))
        line = self._readline_unlocked(timeout=ready_timeout)
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            err = self._drain_stderr()
            self._kill_unlocked()
            raise EasyOcrSubprocessError(
                f"Worker ready handshake failed: {exc}; stderr={err[:500]}"
            ) from exc
        if not msg.get("ok") or not msg.get("ready"):
            err = self._drain_stderr()
            self._kill_unlocked()
            raise EasyOcrSubprocessError(
                f"Worker failed to become ready: {msg}; stderr={err[:500]}"
            )

    def _call_unlocked(self, path: Path, *, timeout: float) -> list:
        assert self._proc and self._proc.stdin and self._proc.stdout
        payload = json.dumps({"cmd": "readtext", "path": str(path)}) + "\n"
        try:
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise EasyOcrSubprocessError(f"worker stdin broken: {exc}") from exc

        line = self._readline_unlocked(timeout=timeout)
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EasyOcrSubprocessError(f"bad worker response: {line[:200]}") from exc
        if not msg.get("ok"):
            raise EasyOcrSubprocessError(str(msg.get("error") or msg))
        return list(msg.get("results") or [])

    def _readline_unlocked(self, *, timeout: float) -> str:
        assert self._proc and self._proc.stdout
        line_holder: list[str | None] = [None]
        err_holder: list[BaseException | None] = [None]

        def _read() -> None:
            try:
                line_holder[0] = self._proc.stdout.readline()  # type: ignore[union-attr]
            except BaseException as exc:  # noqa: BLE001
                err_holder[0] = exc

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        deadline = time.monotonic() + timeout
        while t.is_alive():
            if self._proc.poll() is not None:
                t.join(timeout=1.0)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EasyOcrSubprocessError("worker timed out waiting for response")
            t.join(timeout=min(1.0, remaining))

        if err_holder[0] is not None:
            raise EasyOcrSubprocessError(f"worker read error: {err_holder[0]}")
        line = line_holder[0]
        if line is None:
            err = self._drain_stderr()
            code = self._proc.returncode if self._proc else None
            raise EasyOcrSubprocessError(
                f"worker timed out or died (code={code}); stderr={err[:800]}"
            )
        if line == "":
            err = self._drain_stderr()
            raise EasyOcrSubprocessError(
                f"worker closed stdout; stderr={err[:800]}"
            )
        return line

    def _drain_stderr(self) -> str:
        if not self._proc or not self._proc.stderr:
            return ""
        try:
            # Non-blocking-ish: only read what's already buffered after exit.
            return self._proc.stderr.read() or ""
        except Exception:  # noqa: BLE001
            return ""

    def _kill_unlocked(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    if proc.stdin:
                        proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                        proc.stdin.flush()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


_client: EasyOcrSubprocessClient | None = None
_client_lock = threading.Lock()


def get_easyocr_subprocess_client() -> EasyOcrSubprocessClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = EasyOcrSubprocessClient()
            atexit.register(_client.close)
        return _client
