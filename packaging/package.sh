#!/usr/bin/env bash
# Try to build an AppImage, a Debian package, and an rpm of the Electron shell.
# The Python sidecar is not inside the Electron bundle. Record the real result.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/packaging/out"
mkdir -p "$OUT"
cd "$ROOT/shell/electron"
if ! npm ls electron-builder >/dev/null 2>&1; then
  npm install --no-audit --no-fund --no-save electron-builder || {
    printf 'electron-builder install failed\n' | tee "$OUT/result.txt"
    exit 1
  }
fi
npx electron-builder --linux AppImage deb rpm --config.directories.output="$OUT" >"$OUT/build.log" 2>&1
status=$?
printf 'electron-builder exit %s\n' "$status" | tee "$OUT/result.txt"
exit "$status"
