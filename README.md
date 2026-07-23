# AI Video Editor

Bulk AI-assisted video editor: upload clips, describe edits in natural language, and render with FFmpeg.

## Stack

- **Frontend:** Next.js (React)
- **Backend:** FastAPI + background job worker
- **Editing engines (toggle in UI):**
  - **Bulkcut (My creation):** FFmpeg + EasyOCR (full-frame text find) + Pillow style sampling
  - **Creatomate (Direct API):** Creatomate cloud renders via template / RenderScript
- **LLM:** Mock provider locally, or Ollama (`qwen2.5`)

## Quick start (local)

### Prerequisites

- Python 3.11+
- Node.js 20+
- [FFmpeg](https://ffmpeg.org/download.html) on your `PATH` (or set absolute paths in `backend/.env`)

### Backend

```bash
cd backend
python -m venv .venv
# Windows:
.\.venv\Scripts\activate
pip install -r requirements.txt
copy ..\.env.example .env   # then edit FFMPEG_PATH / FFPROBE_PATH / CREATOMATE_API_KEY if needed
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Creatomate:** set `CREATOMATE_API_KEY` in `backend/.env`. In the UI, toggle **Creatomate**:
upload a video → OCR finds the text (same as Bulkcut) → we auto-build a Creatomate
RenderScript template (source video + cover boxes + new text) → cloud render.
FFmpeg is still needed for frame sampling / OCR.

**EasyOCR first run:** the first text-replace job downloads English detection/recognition models
(into your user cache). That can take a few minutes and needs network access once.

### Frontend

```bash
cd frontend
npm install
# create .env.local:
# NEXT_PUBLIC_API_URL=http://localhost:8000
# NEXT_PUBLIC_API_KEY=dev-api-key-change-me
npm run dev
```

Open http://localhost:3000

### Example prompt

```text
Replace 15 & 16 august to 15 & 16 july
```

The pipeline OCRs sample frames, finds **every** matching occurrence (middle overlay + lower banner),
covers **only the matched glyph span** (not the whole banner line), and redraws just the replacement
wording using sampled fill/font colors and size. Pin icons, year/time, title prefixes, logos, and
banner chrome stay unless you asked to change them.

Multi-pair example:

```text
Replace Sydney to Melbourne, replace 15 & 16 august to 26 & 27 september
```

OCR samples several timestamps, merges hits across frames (so a city found only on
an early frame and a date found only later both get replaced), and retries with an
upscaled/contrast pass when a short city name appears in only one place on a
portrait clip. If it still cannot find multiple overlays, the item **fails** instead
of shipping mixed cities (e.g. Sydney left in the hero while the banner says Melbourne).

**Venue strings:** `replace_text` applies each `to` as written. If you also change the
venue to something that still contains the old city (e.g. `Shangri-La Sydney` after
`Sydney → Melbourne`), that venue line will keep saying Sydney unless you put the
new city in the venue `to` yourself.

## Docker

```bash
docker compose up --build
```

## API health

`GET http://127.0.0.1:8000/health`

## Project layout

```text
backend/     FastAPI app, FFmpeg pipeline, job worker
frontend/    Next.js UI
scripts/     helper scripts
docs/        GitHub Pages site
```

## License

MIT
