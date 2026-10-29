#!/bin/sh
set -e

echo "[init-ollama] Waiting for Ollama at ${OLLAMA_BASE_URL:-http://ollama:11434}..."
until curl -s "${OLLAMA_BASE_URL:-http://ollama:11434}/api/tags" > /dev/null 2>&1; do
  sleep 2
done

echo "[init-ollama] Ollama is ready."

PULL_MODELS="${PULL_MODELS:-bge-m3 qwen2.5:7b}"
for model in $PULL_MODELS; do
  echo "[init-ollama] Ensuring model '$model' is pulled..."
  curl -s -X POST "${OLLAMA_BASE_URL:-http://ollama:11434}/api/pull" -d "{\"name\": \"$model\", \"stream\": false}"
  echo ""
  echo "[init-ollama] Model '$model' pull complete."
done

echo "[init-ollama] All required models are ready."
