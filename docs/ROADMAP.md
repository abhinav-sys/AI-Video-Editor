# AI-Video-Editor Version 2 — Roadmap Tracker

**Status legend:** `pending` · `in_progress` · `done` · `blocked`

| Phase | Name | Status | Started | Completed | Notes |
|-------|------|--------|---------|-----------|-------|
| 0 | Tracking + baseline freeze | done | 2026-07-23 | 2026-07-23 | Docs + golden freeze; 42→48 pytest |
| 1 | Postgres + Celery + Redis | done | 2026-07-23 | 2026-07-23 | Compose + lease + partial status |
| 2 | Scenes + time-gated paint | done | 2026-07-23 | 2026-07-23 | PySceneDetect + enable=between |
| 3 | EditableTemplate + PaddleOCR | done | 2026-07-23 | 2026-07-23 | Schema + OcrProvider + timeline |
| 4 | Tracking + LaMa inpaint | done | 2026-07-23 | 2026-07-23 | IoU tracker + inpaint_service |
| 5 | Vision / logos | done | 2026-07-23 | 2026-07-23 | Heuristic + optional GroundingDINO |
| 6 | Frontend template editor | done | 2026-07-23 | 2026-07-23 | TemplateEditor + PATCH/rerender |
| 7 | Hardening + packaging | done | 2026-07-23 | 2026-07-23 | Fonts, magic bytes, correlation IDs |

## Rule

Do not start Phase N+1 until Phase N exit criteria are green and manual test is confirmed.

## Known bugs (Phase 0 baseline — status after V2)

1. **Whole-video paint** — fixed (Phase 2 `enable=between`).
2. **JobStatus tautology** — fixed (`partial` / `completed` / `failed`).
3. **Stuck `running` jobs** — fixed (lease + reconciler).
4. **SQLite default** — Docker Compose now defaults to Postgres; local SQLite still OK with `USE_INLINE_WORKER=true`.
5. **Windows-first fonts** — fixed (bundled `assets/fonts` preferred).

## Golden baseline

- Path: `backend/storage/outputs/_audit/golden_final/`
- Key artifacts: `audit.json`, `matches.json`, `edited.mp4`, `regions.json`

## Architecture target

Upload → ingest (scenes) → detect (OCR) → vision → track → EditableTemplate → user edits → inpaint → FFmpeg render → zip.
