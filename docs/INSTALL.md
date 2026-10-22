# Install GrokDeck

GrokDeck uses the same source installer on Fedora and Debian. It needs Node 22.12 or newer, Python 3.11 or newer, npm, pnpm, `rsvg-convert`, and the Electron desktop libraries. A graphical session is needed to open the window. XWayland is used under GNOME.

## System packages

**Fedora**

```bash
sudo dnf install nodejs npm python3 python3-pip python3-virtualenv librsvg2-tools \
  libsecret nss atk at-spi2-atk mesa-libgbm alsa-lib
```

**Debian 12 or newer**

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip librsvg2-bin \
  libsecret-1-0 libnss3 libatk-bridge2.0-0 libgbm1 libasound2
```

Debian 12's default Node release is too old for this project. Install Node 22.12 or newer from [Node.js](https://nodejs.org/en/download) and confirm `node --version` and `npm --version`. Some newer Debian-family systems use `libasound2t64` in place of `libasound2`.

On either distro, install pnpm 9.15.9 with `npm install --global pnpm@9.15.9`, or use a user-level npm prefix if you do not have global install permission.

## Install and launch

From the GrokDeck checkout:

```bash
bash packaging/install.sh
~/.local/bin/grokdeck
```

The launcher also appears as **GrokDeck** in the desktop app menu. If `~/.local/bin` is on your `PATH`, run `grokdeck`. The installer puts application code and local data under `~/.local/share/grokdeck`, or under `GROKDECK_HOME` when set. It backs up `deck.db` before an update. Close GrokDeck and finish active turns before reinstalling.

Sign in to the embedded Grok web page for web chat. To use Build with your subscription, install and sign in to the official Grok CLI separately. GrokDeck finds `~/.grok/bin/grok`, or you can set `GROKDECK_GROK_BIN`. API providers need their own keys in Settings.

To update, pull new source files and rerun `bash packaging/install.sh`. The installed copy does not change when you edit the checkout.

The experimental `packaging/package.sh` builds Electron-only RPM, DEB, and AppImage files. They do not contain the Python sidecar and are not supported as standalone installs yet.

[Back to GrokDeck](../README.md)
