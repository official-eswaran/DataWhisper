#!/usr/bin/env bash
# End-to-end runner (issue #20). Brings up the backend, lets Playwright bring up
# the frontend, runs the smoke test, and tears the backend down again.
#
# Prerequisites:
#   - Python deps installed (backend/requirements*.txt)
#   - Ollama running with the model pulled (see backend LLM_MODEL, default
#     llama3.2:3b). The test makes a REAL inference call.
#   - Frontend deps installed (npm ci) and Playwright browsers
#     (npx playwright install chromium).
#
# Usage (from frontend/):  npm run test:e2e
set -euo pipefail

FRONTEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$FRONTEND_DIR/.." && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
WORK="$(mktemp -d -t datawhisper-e2e-XXXXXX)"
BACKEND_PID=""

cleanup() {
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
MODEL="${LLM_MODEL:-llama3.2:3b}"

echo "e2e: checking Ollama…"
if ! curl -sf -m 3 "$OLLAMA_BASE_URL/api/tags" >/dev/null; then
  echo "e2e: Ollama is not reachable — start it and pull the model first." >&2
  exit 1
fi

# Warm the model: force its weights into RAM now, before Playwright starts, so
# the in-test query is not the one paying for the cold model load. Without this,
# the first (cold) inference on a CPU runner blows the test's expect timeout, the
# response lands after the assertion gave up, and only the cache-warmed retry
# passes — the suite then certifies the warm path while a cold-path regression
# goes undetected (issue #45). Ollama keeps the model resident for ~5 min, well
# past frontend startup + register + upload, so it is still loaded at query time.
echo "e2e: warming '$MODEL' (loads weights into RAM)…"
if curl -sf -m 180 "$OLLAMA_BASE_URL/api/generate" \
     -d "{\"model\":\"$MODEL\",\"prompt\":\"ready?\",\"stream\":false}" >/dev/null; then
  echo "e2e: model warm."
else
  echo "e2e: warm-up call failed — the first in-test query will pay model load." >&2
fi

echo "e2e: starting backend on :8000…"
(
  cd "$BACKEND_DIR"
  SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_hex(32))')" \
  DEBUG=true \
  DATABASE_URL="sqlite:///$WORK/e2e.db" \
  DATABASE_DIR="$WORK/db" \
  UPLOAD_DIR="$WORK/uploads" \
  ALLOWED_ORIGINS="*" \
  exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
) >"$WORK/backend.log" 2>&1 &
BACKEND_PID=$!

echo "e2e: waiting for backend readiness…"
for i in $(seq 1 30); do
  if curl -sf -m 3 http://127.0.0.1:8000/health/ready >/dev/null; then
    break
  fi
  if [ "$i" = 30 ]; then
    echo "e2e: backend did not become ready. Log:" >&2
    tail -n 40 "$WORK/backend.log" >&2
    exit 1
  fi
  sleep 1
done

echo "e2e: running Playwright…"
cd "$FRONTEND_DIR"
npx playwright test "$@"
