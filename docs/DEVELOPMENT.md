# Develop GrokDeck

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
(cd frontend && pnpm install --frozen-lockfile)
(cd shell/electron && npm ci)
(cd frontend && pnpm build)
./launch.sh
```

`launch.sh` runs the checkout. It starts the local FastAPI sidecar and then opens Electron. Web chat and API providers do not require the Grok CLI; Build does.

For frontend work, start the sidecar with `.venv/bin/python -m uvicorn sidecar.grokdeck_server:app --host 127.0.0.1 --port 9488`, then run `pnpm dev` in `frontend`. The Vite page is a browser preview; use Electron to check window controls and embedded Grok chat.

App data defaults to `~/.local/share/grokdeck`. Set `GROKDECK_HOME` and `GROKDECK_USER_DATA` to isolated directories for local tests. Do not commit either directory, logs, keys, or browser profiles.

[Back to GrokDeck](../README.md)
