#!/usr/bin/env python3
"""CLI: run OCR audit gates on a video + from/to pairs.

Example:
  python -m scripts.ocr_audit_run ^
    --video "storage/uploads/.../clip.mp4" ^
    --from Sydney --to Melbourne ^
    --from "15 & 16 august" --to "15 & 16 july" ^
    --render
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

# Allow `python scripts/ocr_audit_run.py` from backend/
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.api.schemas.edits import EditInstructions, TextReplace
from app.services.ffmpeg_service import FFmpegService
from app.services.ocr_audit import run_full_audit
from app.services.storage import StorageService


def _parse_pairs(args: argparse.Namespace) -> list[TextReplace]:
    if len(args.fr) != len(args.to):
        raise SystemExit("--from and --to counts must match")
    if not args.fr:
        raise SystemExit("Provide at least one --from / --to pair")
    return [
        TextReplace.model_validate({"from": f, "to": t}) for f, t in zip(args.fr, args.to)
    ]


async def _main() -> int:
    parser = argparse.ArgumentParser(description="OCR step-by-step audit tracker")
    parser.add_argument("--video", required=True, type=Path, help="Input video path")
    parser.add_argument("--from", dest="fr", action="append", default=[], help="Text to find")
    parser.add_argument("--to", dest="to", action="append", default=[], help="Replacement text")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Audit output dir (default: storage/outputs/_audit/<uuid>)",
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="Also run full Bulkcut FFmpeg render into the audit dir",
    )
    parser.add_argument(
        "--boost",
        action="store_true",
        help="Use boosted (2-pass) OCR — slower / heavier",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip re-OCR of painted still",
    )
    parser.add_argument("--upload-id", default="audit", help="Upload id for asset resolve")
    args = parser.parse_args()

    video = args.video.resolve()
    if not video.is_file():
        print(f"Video not found: {video}", file=sys.stderr)
        return 2

    pairs = _parse_pairs(args)
    storage = StorageService()
    storage.ensure_dirs()
    out = args.out
    if out is None:
        out = storage.outputs / "_audit" / str(uuid.uuid4())
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    ffmpeg = FFmpegService(storage)
    print(f"Audit -> {out}")
    report = await run_full_audit(
        video_path=video,
        replacements=pairs,
        out_dir=out,
        ffmpeg_service=ffmpeg,
        use_boost=args.boost,
        verify=not args.no_verify,
    )

    if args.render and report.regions:
        instructions = EditInstructions(replace_text=pairs)
        output_mp4 = out / "edited.mp4"
        print(f"Rendering full video -> {output_mp4}")
        result = await ffmpeg.render(
            input_path=video,
            output_path=output_mp4,
            instructions=instructions,
            upload_id=args.upload_id,
            preview_dir=out / "render_previews",
            text_regions=report.regions,
        )
        # Update full_video gate in audit.json
        audit_path = out / "audit.json"
        data = json.loads(audit_path.read_text(encoding="utf-8"))
        for g in data.get("gates", []):
            if g.get("name") == "full_video":
                g["status"] = "pass" if output_mp4.is_file() and result.occurrences > 0 else "fail"
                g["reason"] = (
                    f"edited.mp4 occurrences={result.occurrences}"
                    if output_mp4.is_file()
                    else "edited.mp4 missing"
                )
                g["details"] = {"occurrences": result.occurrences, "path": str(output_mp4)}
        data["ok"] = all(g.get("status") in ("pass", "skip", "warn") for g in data.get("gates", []))
        data["edited_video"] = str(output_mp4) if output_mp4.is_file() else None
        audit_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Render done: occurrences={result.occurrences}")
    elif args.render and not report.regions:
        print("Skip --render: no OCR regions", file=sys.stderr)

    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    # Treat warn as non-fatal for CLI exit
    hard_fail = any(g.status == "fail" for g in report.gates)
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
