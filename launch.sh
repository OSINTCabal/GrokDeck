#!/usr/bin/env bash
# GrokDeck launcher — starts the loopback sidecar, then the Electron shell.
#
# The sidecar owns the grok agent processes and the local SQLite store. The
# Electron window is a thin frameless shell around the SPA it serves.
#
#   grokdeck                 normal launch
#   GROKDECK_PORT=9600 grokdeck
#   GROKDECK_URL=http://127.0.0.1:9600 electron shell/electron   # shell only

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${GROKDECK_PORT:-9488}"
URL="${GROKDECK_URL:-http://127.0.0.1:${PORT}}"
HOME_DIR="${GROKDECK_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/grokdeck}"
PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

export GROKDECK_HOME="$HOME_DIR"
mkdir -p "$HOME_DIR"

log() { printf '\033[2m[grokdeck]\033[0m %s\n' "$1" >&2; }

# The CLI is optional for web chat and API-backed providers.
if ! command -v grok >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/grok" ]; then
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi

cd "$HERE"

# Reuse a healthy sidecar instead of fighting over the port.
if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
  log "reusing sidecar already on $PORT"
else
  log "starting sidecar on 127.0.0.1:$PORT (data: $HOME_DIR)"
  "$PY" -m uvicorn sidecar.grokdeck_server:app \
    --host 127.0.0.1 --port "$PORT" --log-level warning &
  SIDECAR_PID=$!
  trap 'kill "$SIDECAR_PID" 2>/dev/null || true' EXIT INT TERM

  for _ in $(seq 1 60); do
    if curl -fsS "$URL/api/health" >/dev/null 2>&1; then break; fi
    sleep 0.25
  done
  if ! curl -fsS "$URL/api/health" >/dev/null 2>&1; then
    log "sidecar failed to become healthy"
    exit 1
  fi
fi

if ! command -v electron >/dev/null 2>&1; then
  if [ -x "$HERE/shell/electron/node_modules/.bin/electron" ]; then
    "$HERE/shell/electron/node_modules/.bin/electron" "$HERE/shell/electron"
    exit $?
  fi
  log "electron not installed. Run: (cd shell/electron && npm install)"
  exit 1
fi

electron "$HERE/shell/electron"
