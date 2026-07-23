# Phase Log

After each phase, append a dated entry: summary, files touched, tests, known gaps.

---

## Phase 0 — Tracking + baseline freeze (2026-07-23)

### Summary

Living tracker created. Golden audit under `backend/storage/outputs/_audit/golden_final/` frozen as pre-V2 baseline.

### Pytest baseline

Initially `42 passed`; after full V2 suite: `48 passed`.

---

## Phase 1 — Durable infra (2026-07-23)

### Summary

- Postgres + Redis + Celery worker in `docker-compose.yml`
- `USE_INLINE_WORKER` gate; Celery `process_job` task
- Job lease fields + reconciler requeues stale `running`
- `JobStatus.partial` when some items fail
- `dispatch_job` after create when Celery mode

### Files

`config.py`, `models.py`, `004_v2_infra.py`, `celery_app.py`, `tasks.py`, `runner.py`, `pipeline.py`, `creatomate_pipeline.py`, `job_service.py`, `downloads.py`, `docker-compose.yml`, `requirements.txt`, `.env.example`

### Test

Unit suite green. Manual: `docker compose up` + kill API mid-job (user).

---

## Phase 2 — Scenes + time-gating (2026-07-23)

### Summary

- `scene_detect.py` (PySceneDetect with full-duration fallback)
- Per-shot sampling in `locate_text_regions`
- `enable=between` on drawtext/overlay via `ffmpeg_kit.enable_between`
- Regions carry `t_start` / `t_end`

### Test

`test_enable_between_formats`, `test_shot_sample_times_caps` pass.

---

## Phase 3 — EditableTemplate + OCR providers (2026-07-23)

### Summary

- `api/schemas/template.py` EditableTemplate
- `ocr_providers.py` EasyOCR + optional PaddleOCR
- `timeline_service.py` regions ↔ template
- Persist `JobItem.template_json` on render

---

## Phase 4 — Tracking + inpaint (2026-07-23)

### Summary

- `tracking_service.py` IoU tracker (ByteTrack-lite)
- `inpaint_service.py` flat/banner fast path + Telea + optional LaMa

---

## Phase 5 — Vision (2026-07-23)

### Summary

- `vision_service.py` optional GroundingDINO + corner contour heuristic
- Logo entities merged into EditableTemplate

---

## Phase 6 — Frontend editor (2026-07-23)

### Summary

- API: GET/PATCH template, POST rerender
- `TemplateEditor.tsx` on job page when `has_template`

---

## Phase 7 — Hardening (2026-07-23)

### Summary

- Bundled fonts dir + Docker DejaVu/Liberation; OS fonts no longer preferred first
- Upload magic-byte validation + batch size cap
- Typed `FilterGraph` in `ffmpeg_kit.py`
- Correlation IDs on 500 responses
- Optional `HWACCEL=nvenc|qsv`

### Pytest final

`48 passed` (2026-07-23)

### Known gaps for user testing

- Install `celery[redis]`, `psycopg`, `scenedetect` in local venv if using Docker-parity queue (cv2 lock may block scenedetect on Windows while OpenCV is loaded)
- PaddleOCR / LaMa / GroundingDINO are optional extras — enable via flags when packages installed
- Re-run golden visual QA after time-gating to confirm banners only appear in-shot

---
