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

# Install Tectonic for offline/local LaTeX PDF compilation
RUN curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.tectonic-typesetting.net | sh \
    && mv tectonic /usr/local/bin/

# Copy Python dependency files and install
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir -e .

# Install Playwright browser dependencies and chromium
RUN python -m playwright install --with-deps chromium

# Copy application code and built frontend
COPY jobscout/ ./jobscout/
COPY config/ ./config/
COPY templates/ ./templates/
COPY data/ ./data/
COPY --from=frontend-builder /app/web/dist ./web/dist

# Ensure runtime directories exist
RUN mkdir -p /app/data/cover_letters /app/data/embeddings /app/.browser-profiles

EXPOSE 8000

CMD ["uvicorn", "jobscout.server:app", "--host", "0.0.0.0", "--port", "8000"]
