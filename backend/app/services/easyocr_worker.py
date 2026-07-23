"""Long-lived EasyOCR subprocess so native crashes don't kill the API process.

Protocol (JSON lines on stdin/stdout):
  request:  {"cmd":"readtext","path":"/abs/image.png"}
  response: {"ok":true,"results":[[[x,y],...],"text",0.9], ...]}
  or:       {"ok":false,"error":"..."}
  ping:     {"cmd":"ping"} -> {"ok":true,"pong":true}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _run_worker() -> None:
    # Import only in the child so the parent never loads torch/easyocr.
    import easyocr  # type: ignore

    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    # Signal readiness (parent waits for this line).
    sys.stdout.write(json.dumps({"ok": True, "ready": True}) + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps({"ok": False, "error": f"bad json: {exc}"}) + "\n")
            sys.stdout.flush()
            continue

        cmd = (req.get("cmd") or "").strip().lower()
        if cmd == "ping":
            sys.stdout.write(json.dumps({"ok": True, "pong": True}) + "\n")
            sys.stdout.flush()
            continue
        if cmd == "quit":
            sys.stdout.write(json.dumps({"ok": True, "bye": True}) + "\n")
            sys.stdout.flush()
            break
        if cmd != "readtext":
            sys.stdout.write(json.dumps({"ok": False, "error": f"unknown cmd: {cmd}"}) + "\n")
            sys.stdout.flush()
            continue

        path = Path(str(req.get("path") or ""))
        if not path.is_file():
            sys.stdout.write(json.dumps({"ok": False, "error": f"missing file: {path}"}) + "\n")
            sys.stdout.flush()
            continue

        try:
            results = reader.readtext(str(path))
            # Serialize polygons as plain lists for JSON.
            serializable = []
            for item in results or []:
                if not item or len(item) < 3:
                    continue
                bbox, text, conf = item[0], str(item[1]), float(item[2])
                pts = [[float(p[0]), float(p[1])] for p in bbox]
                serializable.append([pts, text, conf])
            sys.stdout.write(json.dumps({"ok": True, "results": serializable}) + "\n")
            sys.stdout.flush()
        except Exception as exc:  # noqa: BLE001 — surface to parent as JSON
            sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    _run_worker()
