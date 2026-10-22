#!/usr/bin/env bash
# GrokDeck user-level install (no root). Builds the frontend, prepares the
# Python environment, then installs the app, launcher, desktop entry and icons.
#
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"                      # project root
APP="${GROKDECK_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/grokdeck}" # app root + data
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
LAUNCHER="$BIN/grokdeck"

say() { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
die() { printf '\033[31mError:\033[0m %s\n' "$1" >&2; exit 1; }

[ "$SRC" != "$APP" ] || die "Install from a source checkout, not the installed app."

# ---- 1. preflight (read-only) ---------------------------------------------
say "Checking prerequisites"
GROK_CMD="${GROKDECK_GROK_BIN:-}"
if [ -z "$GROK_CMD" ] && [ -x "$HOME/.grok/bin/grok" ]; then GROK_CMD="$HOME/.grok/bin/grok"; fi
if [ -z "$GROK_CMD" ]; then GROK_CMD="$(command -v grok || true)"; fi
case "${GROK_CMD:+$("$GROK_CMD" agent --help 2>&1)}" in
  *stdio*) ;;
  *) GROK_CMD=""; printf '    xAI Grok Build CLI not found. Web chat and API providers still work.\n' ;;
esac
command -v node    >/dev/null || die "Node.js not found (need 22.12+). Fedora: sudo dnf install nodejs npm. Debian: install Node 22.12+, then npm."
command -v python3 >/dev/null || die "python3 not found (need 3.11+). Fedora: sudo dnf install python3 python3-pip. Debian: sudo apt install python3 python3-venv python3-pip."
command -v pnpm    >/dev/null || die "pnpm not found. Install it with: npm install -g pnpm@9.15.9"
command -v npm     >/dev/null || die "npm not found. Fedora: sudo dnf install npm. Debian: sudo apt install npm."
command -v rsvg-convert >/dev/null || die "rsvg-convert not found. Fedora: sudo dnf install librsvg2-tools. Debian: sudo apt install librsvg2-bin."
PNPM_VERSION="$(cd "$SRC/frontend" && pnpm --version 2>/dev/null)" || die "Could not read pnpm version. Install pnpm 9.15.9."
[ "$PNPM_VERSION" = "9.15.9" ] || die "pnpm 9.15.9 required, found $PNPM_VERSION. Install it with: npm install -g pnpm@9.15.9"

node -e 'const [M,m]=process.versions.node.split(".").map(Number);
  if (M<22 || (M===22 && m<12)) { console.error("Node 22.12+ required, found "+process.versions.node); process.exit(1); }'
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
  || die "Python 3.11+ required."
[ -f "$SRC/shell/electron/main.js" ] || die "Shell source missing; is this a GrokDeck checkout?"
if [ -n "$GROK_CMD" ]; then
  printf '    grok   %s (%s)\n' "$("$GROK_CMD" --version 2>/dev/null | head -1)" "$GROK_CMD"
fi
printf '    node   %s\n' "$(node --version)"
printf '    pnpm   %s\n' "$PNPM_VERSION"
printf '    python %s\n' "$(python3 --version | cut -d' ' -f2)"

# ---- 2. build --------------------------------------------------------------
say "Building the frontend"
( cd "$SRC/frontend" && pnpm install --frozen-lockfile --prefer-offline && pnpm build )

say "Preparing the Electron shell"
( cd "$SRC/shell/electron" && npm ci --no-audit --no-fund >/dev/null 2>&1 ) \
  || die "npm ci failed in shell/electron"

mkdir -p "$APP"
STAGE="$(mktemp -d "$APP/.install-XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
say "Preparing the Python environment"
python3 -m venv "$STAGE/.venv"
"$STAGE/.venv/bin/pip" install --quiet "$SRC"

# A running sidecar would keep the old code and database open. Stop only the
# PID this app recorded, after the new build is ready.
if [ -f "$APP/sidecar.pid" ]; then
  OLD_PID="$(cat "$APP/sidecar.pid" 2>/dev/null || true)"
  if [[ "$OLD_PID" =~ ^[0-9]+$ ]] \
    && [ "$(readlink -f "/proc/$OLD_PID/cwd" 2>/dev/null || true)" = "$(readlink -f "$APP")" ] \
    && tr '\0' ' ' < "/proc/$OLD_PID/cmdline" 2>/dev/null | grep -Fq 'sidecar.grokdeck_server:app'; then
    say "Stopping the previously installed sidecar (pid $OLD_PID)"
    kill "$OLD_PID" 2>/dev/null || true
    sleep 1
  fi
  rm -f "$APP/sidecar.pid"
fi

# ---- 3. back up user data BEFORE touching the install ----------------------
mkdir -p "$APP/backups"
if [ -f "$APP/deck.db" ] || [ -f "$APP/grokdeck.db" ]; then
  STAMP="$(date +%Y%m%d-%H%M%S)"
  say "Backing up existing data to backups/$STAMP"
  mkdir -p "$APP/backups/$STAMP"
  for f in deck.db deck.db-wal deck.db-shm grokdeck.db grokdeck.db-wal grokdeck.db-shm; do
    [ -f "$APP/$f" ] && cp -a "$APP/$f" "$APP/backups/$STAMP/" || true
  done
fi

# ---- 4. stage then swap, so a failed build never breaks the install --------
say "Installing the app to $APP"
mkdir -p "$BIN" "$APPS" "$ICONS" "$APP/logs"

# Keep the relative .bin/* symlinks under node_modules intact.
cp -a "$SRC/sidecar"          "$STAGE/sidecar"
cp -a "$SRC/frontend/dist"    "$STAGE/dist"
mkdir -p "$STAGE/shell"
cp -a "$SRC/shell/electron"   "$STAGE/shell/electron"

for part in sidecar dist shell .venv; do
  rm -rf "$APP/$part"
  mv "$STAGE/$part" "$APP/$part"
done
# Entry-point scripts keep the temporary venv path in their shebangs after
# the move. Rewrite only that prefix so pip and uvicorn still work.
"$APP/.venv/bin/python" - "$STAGE/.venv/bin" "$APP/.venv/bin" <<'PY'
from pathlib import Path
import sys

old = b"#!" + sys.argv[1].encode() + b"/python"
new = b"#!" + sys.argv[2].encode() + b"/python"
for script in Path(sys.argv[2]).iterdir():
    if script.is_symlink() or not script.is_file():
        continue
    data = script.read_bytes()
    if data.startswith(old):
        script.write_bytes(new + data[len(old):])
PY
rm -rf "$STAGE"
trap - EXIT

# ---- 5. launcher -----------------------------------------------------------
say "Installing the launcher"
if [ -e "$LAUNCHER" ]; then
  cp -a "$LAUNCHER" "$BIN/grokdeck.bak"
  printf '    previous launcher kept as %s\n' "$BIN/grokdeck.bak"
fi
install -m 0755 "$SRC/packaging/bin/grokdeck" "$LAUNCHER"

# ---- 6. desktop entry ------------------------------------------------------
say "Installing the drawer entry"
sed "s|^Exec=.*|Exec=$LAUNCHER %U|" "$SRC/packaging/grokdeck.desktop" > "$APPS/grokdeck.desktop"
chmod 0644 "$APPS/grokdeck.desktop"
if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$APPS/grokdeck.desktop" \
    && printf '    desktop-file-validate: OK\n' \
    || die "desktop-file-validate rejected the entry"
fi

# ---- 7. icons --------------------------------------------------------------
say "Rendering and installing icons"
python3 "$SRC/packaging/icon/render_icons.py" \
  --out "$APP/icon-build" \
  --hicolor "$ICONS" \
  --icon-256 "$APP/grokdeck-icon.png" \
  --electron-icon "$APP/shell/electron/grokdeck-icon.png" >/dev/null
# Keep the canonical svg/256 in the app tree too, for reference.
cp "$SRC/packaging/icon/grokdeck.svg" "$APP/grokdeck-icon.svg"

# ---- 8. caches -------------------------------------------------------------
say "Refreshing desktop and icon caches"
command -v gtk-update-icon-cache   >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$ICONS"  >/dev/null 2>&1 || true
command -v update-desktop-database  >/dev/null 2>&1 && update-desktop-database  "$APPS" >/dev/null 2>&1 || true

cat <<EOF

Installed.

  App        $APP
  Launcher   $LAUNCHER
  Drawer     $APPS/grokdeck.desktop
  Icons      $ICONS/{16,24,32,48,64,128,256,512,1024}x…/apps/grokdeck.png
             $ICONS/scalable/apps/grokdeck.svg
  Data       $APP/deck.db (preserved across installs)

Next steps

  1. Launch "GrokDeck" from the app drawer, or run:  grokdeck
  2. To use Build, install and sign in with the Grok CLI.
     Web chat and API providers work without it.
  3. After editing code in the project folder, re-run this script to push
     the changes into the installed copy.

Notes

  * If GrokDeck does not appear in the drawer immediately, log out and back
    in — GNOME Shell caches the application list.
  * To uninstall: remove $LAUNCHER, $APPS/grokdeck.desktop, the grokdeck*
    entries under $ICONS, and $APP.

EOF
