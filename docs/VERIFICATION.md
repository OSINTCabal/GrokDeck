# Verify GrokDeck

## Repository checks

On October 29, 2026, 83 backend tests passed, along with frontend tests, the production build, Electron helper tests, and the isolated browser workflow. The frontend and Electron lockfiles report zero known vulnerabilities in their npm registry audits. The updates keep React 18 and the existing Electron major version.

Offline checks:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m ruff check sidecar tests e2e
(cd shell/electron && npm test)
(cd frontend && pnpm test)
(cd frontend && pnpm build)
```

The backend suite includes a launcher check that starts a local fixture service and verifies GrokDeck only reuses its own sidecar. An unrelated service on the same port is rejected before Electron starts. GitHub Actions runs backend tests, lint, frontend tests and production build, Electron helper tests, and launcher syntax checks.

The browser UI check uses isolated local data and a fake response stream:

```bash
.venv/bin/python e2e/run_ui.py
```

It refreshes screenshots in `docs/shots`. It does not verify real provider billing, CLI sign-in, or the actual Electron window. For a release, also open the desktop on Fedora and Debian, drag all four edges and corners, maximize and restore, restart to confirm the saved size, and check web chat and Build with accounts you control. Provider tests can spend API quota.

[Back to GrokDeck](../README.md)
