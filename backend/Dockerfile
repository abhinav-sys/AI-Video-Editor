FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bundle fonts for consistent drawtext matching (Phase 7)
RUN mkdir -p /app/assets/fonts \
    && cp -n /usr/share/fonts/truetype/dejavu/*.ttf /app/assets/fonts/ 2>/dev/null || true \
    && cp -n /usr/share/fonts/truetype/liberation/*.ttf /app/assets/fonts/ 2>/dev/null || true

RUN mkdir -p storage/uploads storage/assets storage/outputs storage/zips

ENV PYTHONUNBUFFERED=1
ENV DATABASE_URL=sqlite:///./app.db
ENV STORAGE_ROOT=./storage
ENV FONTS_DIR=/app/assets/fonts
ENV USE_INLINE_WORKER=true

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
