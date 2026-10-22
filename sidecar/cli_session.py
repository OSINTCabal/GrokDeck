"""Sign-in through the local grok CLI.

``grok login --oauth`` opens the system browser at auth.x.ai and writes the
CLI's own credential file. This module never reads that file. It only checks
that the file exists, and it never puts CLI stdout into an API response.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, AsyncIterator

_session: dict[str, Any] = {"proc": None, "state": "idle", "message": ""}

_EFFORT = {"fast": "low", "auto": "medium", "expert": "high", "heavy": "xhigh"}
_CHAT_RULE = (
    "You are Grok in a conversation. Reply in plain language. "
    "Do not edit files, do not run commands, and do not use tools."
)


def grok_bin() -> str | None:
    override = os.environ.get("GROKDECK_GROK_BIN")
    if override is not None:
        path = Path(override)
        if path.is_file() and os.access(path, os.X_OK) and _supports_agent(str(path)):
            return str(path)
        return None
    for candidate in (Path.home() / ".grok/bin/grok", shutil.which("grok")):
        if candidate and _supports_agent(str(candidate)):
            return str(candidate)
    return None


@lru_cache(maxsize=8)
def _agent_help(path: str, changed: int) -> bool:
    try:
        result = subprocess.run([path, "agent", "--help"], capture_output=True, text=True, timeout=3, check=False)
        return result.returncode == 0 and "stdio" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def _supports_agent(path: str) -> bool:
    try:
        return _agent_help(path, Path(path).resolve().stat().st_mtime_ns)
    except OSError:
        return False


def auth_file() -> Path:
    root = os.environ.get("GROKDECK_GROK_HOME")
    base = Path(root) if root else Path.home() / ".grok"
    return base / "auth.json"


def signed_in() -> bool:
    path = auth_file()
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def account_status() -> dict[str, Any]:
    proc = _session.get("proc")
    state = str(_session.get("state") or "idle")
    if proc is not None and getattr(proc, "returncode", None) is None and state == "waiting":
        public_state = "waiting"
    elif signed_in():
        public_state = "signed-in"
    elif state == "failed":
        public_state = "failed"
    else:
        public_state = "idle"
    return {
        "signedIn": signed_in(),
        "cli": grok_bin() is not None,
        "state": public_state,
        "message": str(_session.get("message") or ""),
    }


def _browser_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    env.setdefault("XDG_SESSION_TYPE", "wayland")
    env["NO_COLOR"] = "1"
    return env


async def _run(args: list[str], timeout: float) -> tuple[int, str]:
    binary = grok_bin()
    if not binary:
        return 127, ""
    proc = await asyncio.create_subprocess_exec(
        binary,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_browser_env(),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, ""
    text = (out or b"").decode("utf-8", "replace")
    return proc.returncode or 0, text


def _safe_notice(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned or len(cleaned) > 180:
            continue
        lower = cleaned.lower()
        if any(word in lower for word in ("token", "secret", "auth.json", "bearer", "cookie")):
            continue
        if cleaned.count(".") >= 2 and " " not in cleaned:
            continue
        if cleaned.startswith("http"):
            continue
        return cleaned
    return ""


async def begin_login() -> dict[str, Any]:
    if grok_bin() is None:
        _session["state"] = "failed"
        _session["message"] = "Grok CLI was not found."
        return account_status()
    current = _session.get("proc")
    if current is not None and getattr(current, "returncode", None) is None:
        return account_status()
    proc = await asyncio.create_subprocess_exec(
        grok_bin() or "grok",
        "login",
        "--oauth",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env=_browser_env(),
        start_new_session=True,
    )
    _session["proc"] = proc
    _session["state"] = "waiting"
    _session["message"] = "Opening your browser to sign in."
    try:
        await asyncio.wait_for(proc.wait(), 0.4)
    except asyncio.TimeoutError:
        asyncio.create_task(_watch_login(proc))
        return account_status()
    _finish_login(proc.returncode or 0)
    return account_status()


async def _watch_login(proc: asyncio.subprocess.Process) -> None:
    code = await proc.wait()
    if _session.get("proc") is proc:
        _finish_login(code)


def _finish_login(code: int) -> None:
    _session["proc"] = None
    if signed_in():
        _session["state"] = "signed-in"
        _session["message"] = ""
        return
    _session["state"] = "failed"
    _session["message"] = "Sign-in did not finish." if code == 0 else "The browser sign-in stopped."


async def cancel_login() -> dict[str, Any]:
    proc = _session.get("proc")
    _session["proc"] = None
    if proc is not None and getattr(proc, "returncode", None) is None:
        proc.kill()
        await proc.wait()
    if not signed_in():
        _session["state"] = "idle"
        _session["message"] = ""
    return account_status()


async def sign_out() -> dict[str, Any]:
    await cancel_login()
    if grok_bin() is None:
        _session["state"] = "failed"
        _session["message"] = "Grok CLI was not found."
        return account_status()
    code, text = await _run(["logout"], 30)
    _session["proc"] = None
    if signed_in():
        _session["state"] = "failed"
        _session["message"] = _safe_notice(text) or "Sign-out did not finish."
    else:
        _session["state"] = "idle"
        _session["message"] = "" if code == 0 else (_safe_notice(text) or "")
    return account_status()


def _plain_transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            if isinstance(content, str) and content.strip():
                lines.append(content.strip())
            continue
        if role not in ("user", "assistant"):
            continue
        if isinstance(content, list):
            bits = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
            content = "\n".join(bit for bit in bits if bit)
        if not isinstance(content, str) or not content.strip():
            continue
        lines.append(f"{role}: {content.strip()}")
    text = "\n\n".join(lines)
    return text[-12_000:]


async def stream_reply(messages: list[dict[str, Any]], mode: str, cancel: asyncio.Event) -> AsyncIterator[dict[str, str]]:
    """One CLI chat turn. Yields token and reasoning events. No prompt is logged."""
    binary = grok_bin()
    if not binary:
        yield {"type": "error", "message": "Grok CLI was not found. Nothing was sent."}
        return
    if any(isinstance(message.get("content"), list) and any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in message["content"]
    ) for message in messages):
        yield {"type": "error", "message": "Grok CLI chat cannot send images. Use Grok web chat or an xAI API key."}
        return
    prompt = _plain_transcript(messages)
    if not prompt.strip():
        yield {"type": "error", "message": "Nothing to send."}
        return
    handle = tempfile.NamedTemporaryFile("w", prefix="grokdeck-prompt-", delete=False, encoding="utf-8")
    path = handle.name
    proc: asyncio.subprocess.Process | None = None
    try:
        handle.write(prompt)
        handle.close()
        os.chmod(path, 0o600)
        argv = [
            binary,
            "--prompt-file", path,
            "--output-format", "streaming-json",
            "--verbatim",
            "--max-turns", "1",
            "--no-plan",
            "--system-prompt-override", _CHAT_RULE,
            "--reasoning-effort", _EFFORT.get(mode, "medium"),
        ]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_browser_env(),
            start_new_session=True,
        )
        assert proc.stdout is not None
        produced = False
        plain: list[str] = []
        wait = 25.0
        while True:
            if cancel.is_set():
                proc.kill()
                await proc.wait()
                yield {"type": "done", "stop": "stopped"}
                return
            read_task = asyncio.create_task(proc.stdout.readline())
            stop_task = asyncio.create_task(cancel.wait())
            try:
                done, _ = await asyncio.wait(
                    {read_task, stop_task}, timeout=wait, return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for task in (read_task, stop_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(read_task, stop_task, return_exceptions=True)
            if stop_task in done:
                proc.kill()
                await proc.wait()
                yield {"type": "done", "stop": "stopped"}
                return
            if read_task not in done:
                proc.kill()
                await proc.wait()
                yield {"type": "error", "message": "Grok took too long to answer. Nothing else was sent."}
                return
            line = read_task.result()
            wait = 90.0
            if not line:
                break
            decoded = line.decode("utf-8", "replace").strip()
            if not decoded:
                continue
            event = _parse_line(decoded)
            if event is None:
                notice = "" if decoded.startswith("{") else _safe_notice(decoded)
                if notice:
                    plain.append(notice)
                continue
            produced = True
            yield event
        code = await proc.wait()
        if cancel.is_set():
            yield {"type": "done", "stop": "stopped"}
            return
        if not produced and plain and code == 0:
            yield {"type": "token", "text": "\n".join(plain)[:8000]}
            produced = True
        if not produced:
            yield {"type": "error", "message": "The Grok CLI did not answer. Nothing else was sent."}
            return
        yield {"type": "done", "stop": "stop"}
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        try:
            os.unlink(path)
        except OSError:
            pass


def _parse_line(line: str) -> dict[str, str] | None:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") in ("text", "thought") and isinstance(obj.get("data"), str):
        return {"type": "token" if obj["type"] == "text" else "reasoning", "text": obj["data"]}
    if obj.get("type") == "error":
        return {"type": "error", "message": "Grok CLI reported an error. Check your sign-in and usage."}
    update = None
    if obj.get("method") == "session/update":
        update = (obj.get("params") or {}).get("update")
    elif isinstance(obj.get("update"), dict):
        update = obj["update"]
    elif "sessionUpdate" in obj:
        update = obj
    if not isinstance(update, dict):
        return None
    kind = update.get("sessionUpdate")
    text = str((update.get("content") or {}).get("text") or "")
    if not text:
        return None
    if kind == "agent_message_chunk":
        return {"type": "token", "text": text}
    if kind == "agent_thought_chunk":
        return {"type": "reasoning", "text": text}
    return None
