# AI Video Editor

Bulk AI-assisted video editor: upload clips, describe edits in natural language, and render with FFmpeg.

## Stack

- **Frontend:** Next.js (React)
- **Backend:** FastAPI + background job worker
- **Editing:** FFmpeg (text replace in banners, logos, watermarks)
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
copy ..\.env.example .env   # then edit FFMPEG_PATH / FFPROBE_PATH if needed
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

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
Replace 15 & 16 august to 26 & 27 september
```

The pipeline detects the date line inside lower-third banners and redraws the replacement text in place.

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
