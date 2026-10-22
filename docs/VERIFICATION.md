# Verify GrokDeck

Offline checks:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m ruff check sidecar tests e2e
(cd shell/electron && npm test)
(cd frontend && pnpm build)
```

The browser UI check uses isolated local data and a fake response stream:

```bash
.venv/bin/python e2e/run_ui.py
```

It refreshes screenshots in `docs/shots`. It does not verify real provider billing, CLI sign-in, or the actual Electron window. For a release, also open the desktop on Fedora and Debian, drag all four edges and corners, maximize and restore, restart to confirm the saved size, and check web chat and Build with accounts you control. Provider tests can spend API quota.

[Back to GrokDeck](../README.md)
