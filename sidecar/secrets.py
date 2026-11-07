"""API key storage.

Production uses libsecret through `secret-tool`. The key is never written to
the database, the repo, or a log line. Tests set GROKDECK_SECRET_PATH to a
temp file; that path is ignored unless GROKDECK_TESTING=1.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

_SERVICE = "grokdeck"
_ACCOUNT = "xai"
_LABEL = "GrokDeck xAI API key"
_PROVIDER_ACCOUNT = "provider:"
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def _testing_file() -> Path | None:
    if os.environ.get("GROKDECK_TESTING") != "1":
        return None
    raw = os.environ.get("GROKDECK_SECRET_PATH")
    if not raw:
        return None
    return Path(raw)


def _provider_testing_file() -> Path | None:
    test = _testing_file()
    return test.with_name(test.name + ".providers") if test is not None else None


def _provider_id(provider_id: str) -> str:
    if not _PROVIDER_ID.fullmatch(provider_id):
        raise ValueError("invalid provider id")
    return provider_id


def configured() -> bool:
    return get_key() is not None


def get_key() -> str | None:
    """Return the key, or None. Callers must not log the return value."""
    test = _testing_file()
    if test is not None:
        if not test.exists():
            return None
        text = test.read_text(encoding="utf-8").strip()
        return text or None
    try:
        proc = subprocess.run(
            ["secret-tool", "lookup", "service", _SERVICE, "account", _ACCOUNT],
            capture_output=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is not None and proc.returncode == 0:
        text = proc.stdout.decode("utf-8", "replace").strip()
        if text:
            return text
    env = os.environ.get("XAI_API_KEY", "").strip()
    return env or None


def set_key(key: str) -> None:
    key = key.strip()
    if not key or "\n" in key or "\r" in key or len(key) > 400:
        raise ValueError("refusing to store that key")
    test = _testing_file()
    if test is not None:
        test.parent.mkdir(parents=True, exist_ok=True)
        test.write_text(key, encoding="utf-8")
        test.chmod(0o600)
        return
    try:
        subprocess.run(
            ["secret-tool", "store", f"--label={_LABEL}", "service", _SERVICE, "account", _ACCOUNT],
            input=key.encode("utf-8"),
            capture_output=True,
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise RuntimeError("libsecret rejected the key") from exc


def clear_key() -> None:
    test = _testing_file()
    if test is not None:
        test.unlink(missing_ok=True)
        return
    subprocess.run(
        ["secret-tool", "clear", "service", _SERVICE, "account", _ACCOUNT],
        capture_output=True,
        timeout=8,
        check=False,
    )


def provider_configured(provider_id: str) -> bool:
    return get_provider_key(provider_id) is not None


def get_provider_key(provider_id: str) -> str | None:
    provider_id = _provider_id(provider_id)
    test = _provider_testing_file()
    if test is not None:
        try:
            value = json.loads(test.read_text(encoding="utf-8")).get(provider_id)
        except (OSError, ValueError, json.JSONDecodeError):
            value = None
        return value if isinstance(value, str) and value else None
    try:
        proc = subprocess.run(
            ["secret-tool", "lookup", "service", _SERVICE, "account", _PROVIDER_ACCOUNT + provider_id],
            capture_output=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = proc.stdout.decode("utf-8", "replace").strip() if proc.returncode == 0 else ""
    return value or None


def set_provider_key(provider_id: str, key: str) -> None:
    provider_id = _provider_id(provider_id)
    key = key.strip()
    if not key or "\n" in key or "\r" in key or len(key) > 512:
        raise ValueError("refusing to store that key")
    test = _provider_testing_file()
    if test is not None:
        values: dict[str, str] = {}
        try:
            old = json.loads(test.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                values = {str(name): str(value) for name, value in old.items() if isinstance(value, str)}
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        test.parent.mkdir(parents=True, exist_ok=True)
        values[provider_id] = key
        test.write_text(json.dumps(values), encoding="utf-8")
        test.chmod(0o600)
        return
    try:
        subprocess.run(
            ["secret-tool", "store", f"--label=GrokDeck {provider_id} API key", "service", _SERVICE,
             "account", _PROVIDER_ACCOUNT + provider_id],
            input=key.encode("utf-8"),
            capture_output=True,
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise RuntimeError("libsecret rejected the key") from exc


def clear_provider_key(provider_id: str) -> None:
    provider_id = _provider_id(provider_id)
    test = _provider_testing_file()
    if test is not None:
        if not test.exists():
            return
        try:
            values = json.loads(test.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            values = {}
        if isinstance(values, dict):
            values.pop(provider_id, None)
            test.write_text(json.dumps(values), encoding="utf-8")
            test.chmod(0o600)
        return
    subprocess.run(
        ["secret-tool", "clear", "service", _SERVICE, "account", _PROVIDER_ACCOUNT + provider_id],
        capture_output=True,
        timeout=8,
        check=False,
    )
