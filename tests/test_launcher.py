from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "packaging/bin/grokdeck"


@pytest.mark.parametrize("app_name,expected", [("grokdeck", 0), ("another-service", 1)])
def test_launcher_only_reuses_its_own_sidecar(tmp_path, app_name, expected):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "app": app_name}).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    root = tmp_path / "app"
    (root / "sidecar").mkdir(parents=True)
    (root / "sidecar/grokdeck_server.py").touch()
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").symlink_to(sys.executable)
    electron = root / "shell/electron/node_modules/.bin/electron"
    electron.parent.mkdir(parents=True)
    electron.write_text('#!/bin/sh\ntouch "$GROKDECK_HOME/electron-started"\n')
    electron.chmod(0o755)
    port = str(server.server_port)
    try:
        result = subprocess.run(
            ["bash", str(LAUNCHER)],
            env={**os.environ, "GROKDECK_HOME": str(root), "GROKDECK_PORT": port,
                 "GROKDECK_URL": "http://127.0.0.1:" + port},
            capture_output=True, text=True, timeout=10,
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    assert result.returncode == expected, result.stderr
    assert (root / "electron-started").exists() is (expected == 0)
    assert not (root / "sidecar.pid").exists()
    if expected:
        assert "already in use by another application" in result.stderr
