#!/usr/bin/env bash
# End-to-end runner (issue #20). Brings up the backend, lets Playwright bring up
# the frontend, runs the suite, and tears the backend down again.
#
# Prerequisites:
#   - Python deps installed (backend/requirements*.txt), or a backend/.venv
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
# The paths below are handed to Python, not to the shell. Under Git Bash/MSYS
# the shell's POSIX view ("/tmp/...") is not a path the Windows interpreter can
# resolve, so translate once here; on Linux/macOS cygpath is absent and $WORK is
# already the native path.
if command -v cygpath >/dev/null 2>&1; then
  WORK_NATIVE="$(cygpath -m "$WORK")"
else
  WORK_NATIVE="$WORK"
fi
BACKEND_PID=""

cleanup() {
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
  # Give the interpreter a moment to let go of the SQLite file before the
  # directory goes; a still-open handle is not worth failing the run over,
  # which is what an unguarded rm under `set -e` would do.
  [ -n "$BACKEND_PID" ] && wait "$BACKEND_PID" 2>/dev/null || true
  rm -rf "$WORK" 2>/dev/null || true
}
trap cleanup EXIT

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
MODEL="${LLM_MODEL:-llama3.2:3b}"

# The backend deps live in backend/.venv on a developer box; a CI image usually
# has them on the bare interpreter. Prefer the venv when it is there, so this
# runs in both places without anyone having to activate anything first.
if [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
  PY="$BACKEND_DIR/.venv/bin/python"
elif [ -x "$BACKEND_DIR/.venv/Scripts/python.exe" ]; then
  PY="$BACKEND_DIR/.venv/Scripts/python.exe"
else
  PY="${PYTHON:-python3}"
fi

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

# Why the extra backend env below — the database is still a throwaway under
# $WORK, and is still empty when the app starts:
#
#   SEED_DEMO_DATA / ADMIN_PASSWORD / MANAGER_PASSWORD — init_db seeds the demo
#   org (ceo = owner, manager = member) only into an EMPTY database, which is
#   exactly what this runner hands it. That gives the suite two known accounts
#   with two different roles, so the role-gated screens (Admin console, audit
#   trail) can be driven from both sides without registering a second org for
#   each of them. Specs that lock, disable or delete an account still register
#   their own throwaway identity — see e2e/helpers.js.
#
#   RATE_LIMIT_* — the shipped limits are sized for humans (register is 5/HOUR,
#   login 10/minute) and the whole suite arrives from one IP inside a couple of
#   minutes. Left alone they fail specs for the wrong reason: the spec asserting
#   the per-ACCOUNT lockout after MAX_LOGIN_ATTEMPTS would instead be served
#   slowapi's per-IP 429 — a different message from a different layer. Raising
#   them keeps each spec about the thing it names. MAX_LOGIN_ATTEMPTS is
#   per-account and is deliberately left at its default of 5.
echo "e2e: starting backend on :8000…"
(
  cd "$BACKEND_DIR"
  SECRET_KEY="$("$PY" -c 'import secrets;print(secrets.token_hex(32))')" \
  DEBUG=true \
  DATABASE_URL="sqlite:///$WORK_NATIVE/e2e.db" \
  DATABASE_DIR="$WORK_NATIVE/db" \
  UPLOAD_DIR="$WORK_NATIVE/uploads" \
  ALLOWED_ORIGINS="*" \
  SEED_DEMO_DATA=true \
  ADMIN_PASSWORD="Admin@2024" \
  MANAGER_PASSWORD="Manager@2024" \
  RATE_LIMIT_LOGIN="1000/minute" \
  RATE_LIMIT_REGISTER="1000/hour" \
  RATE_LIMIT_QUERY="1000/minute" \
  RATE_LIMIT_UPLOAD="1000/minute" \
  exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
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
