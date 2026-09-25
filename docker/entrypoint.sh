#!/bin/sh
set -e

OLLAMA_HOST_URL="${OLLAMA_HOST:-http://ollama:11434}"
MODEL="${CELLTRACE_MODEL:-qwen2.5:3b-instruct}"

echo "waiting for ollama at ${OLLAMA_HOST_URL} ..."
until curl -sf "${OLLAMA_HOST_URL}/api/tags" > /dev/null; do
  sleep 2
done

echo "ensuring model ${MODEL} is pulled (no-op if already present) ..."
curl -sf -X POST "${OLLAMA_HOST_URL}/api/pull" -d "{\"model\": \"${MODEL}\"}" || true

exec "$@"
