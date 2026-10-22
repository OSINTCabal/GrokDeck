#!/usr/bin/env bash
# Web mode: local sidecar on 127.0.0.1:$GROKDECK_PORT, then the Vite app.
# A sidecar this script starts is stopped when Vite exits. An already healthy
# sidecar is left alone.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${GROKDECK_PORT:-9488}"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
cd "$ROOT"
STARTED=0
if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  printf '[grokdeck] reusing sidecar on %s\n' "$PORT"
else
  printf '[grokdeck] starting sidecar on 127.0.0.1:%s\n' "$PORT"
  "$PY" -m uvicorn sidecar.grokdeck_server:app --host 127.0.0.1 --port "$PORT" --log-level warning &
  SIDECAR_PID=$!
  STARTED=1
  for _ in $(seq 1 40); do
    if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.25
  done
  if ! curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    kill "$SIDECAR_PID" 2>/dev/null || true
    printf '[grokdeck] sidecar did not become healthy\n' >&2
    exit 1
  fi
fi
if [ "$STARTED" = 1 ]; then
  trap 'kill "$SIDECAR_PID" 2>/dev/null || true' EXIT INT TERM
fi
cd "$ROOT/frontend"
exec pnpm dev
