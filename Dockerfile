# Stage 1: Build Frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: Python Application
FROM python:3.12-slim AS app

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies: Tesseract OCR (with eng & deu), curl, fontconfig, CA certs
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    fontconfig \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-deu \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Tectonic for offline/local LaTeX PDF compilation (static musl
# builds, pinned; the drop-installer picks a gnu arm64 asset that does not
# exist, so fetch the release directly per architecture).
RUN ARCH="$(dpkg --print-architecture)" \
    && case "$ARCH" in \
         amd64) TARGET=x86_64-unknown-linux-musl ;; \
         arm64) TARGET=aarch64-unknown-linux-musl ;; \
         *) echo "unsupported architecture: $ARCH" && exit 1 ;; \
       esac \
    && curl --proto '=https' --tlsv1.2 -fsSL \
       "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-${TARGET}.tar.gz" \
       | tar xz -C /usr/local/bin tectonic \
    && tectonic --version

# Copy Python dependency files and install
COPY pyproject.toml README.md ./
COPY jobscout/ ./jobscout/
RUN pip install --no-cache-dir .

# Install Playwright browser dependencies and chromium
RUN python -m playwright install --with-deps chromium

# Copy application code and built frontend
COPY config/ ./config/
COPY templates/ ./templates/
COPY --from=frontend-builder /app/web/dist ./web/dist

# Everything persistent lives under /app/data: mount a volume there and the
# jobs CSV, CVs, cover letters, caches, the Setup-view secrets and browser
# profiles all survive restarts and redeploys.
ENV JOBSCOUT_HOST=0.0.0.0 \
    JOBSCOUT_PORT=8000 \
    JOBSCOUT_USER_SETTINGS=/app/data/settings.local.json \
    JOBSCOUT_GOOGLE_SA_FILE=/app/data/google-service-account.json \
    COVER_LETTER_PROFILE=/app/data/profile.yaml \
    JOBSCOUT_CV_MASTER=/app/data/cv.master.yaml \
    BROWSER_PROFILE_DIR=/app/data/browser-profiles
RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "jobscout.ui.server:app", "--host", "0.0.0.0", "--port", "8000"]
