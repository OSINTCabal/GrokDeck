"""ACP bridge to the grok CLI agent.

One long-lived ``grok agent stdio`` (or a test-provided fake from
``GROKDECK_ACP_BIN``) is spawned on demand. The agent speaks JSON-RPC 2.0,
newline-delimited, on stdin/stdout. The public surface maps ACP frames onto
the four routes GrokDeck needs:

  * ``list_sessions()``  - ``session/list``
  * ``open_session()``  - ``session/new`` (instructions stash for one prompt)
  * ``load_session()``  - ``session/load`` plus the ``session/update`` frames
                          the agent emits before the response. They are
                          folded into a public message list with a ``verbose``
                          field. Tool updates are merged, never blanked.
  * ``prompt()``        - ``session/prompt`` streamed as token / reasoning /
                          tool / permission / title / command / done events
  * ``cancel()``        - ``session/cancel`` notification
  * ``answer_permission()`` - reply to a parked ``session/request_permission``

Errors are short public messages. The agent's stderr is piped to ``DEVNULL``
so the bridge cannot echo it back to a caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

# ---- errors ----------------------------------------------------------------


class AcpError(Exception):
    """A short public ACP error. Never contains stderr or credentials."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---- timeouts --------------------------------------------------------------

LIST_TIMEOUT = 20.0
NEW_TIMEOUT = 40.0
LOAD_TIMEOUT = 40.0
PROMPT_TIMEOUT = 1800.0
PROMPT_IDLE_TIMEOUT = 45.0
HANDSHAKE_TIMEOUT = 60.0

# asyncio's default 64 KiB stream limit kills the reader on the first large
# ACP frame. Raise generously.
STREAM_LIMIT = 16 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TERMINAL_OUTPUT = 1 * 1024 * 1024
APPROVAL_TIMEOUT = 120.0
BUILD_MODES = frozenset({"ask", "plan", "accept-edits", "full-auto"})


# ---- argv ------------------------------------------------------------------


def _argv_from_env() -> list[str]:
    """Resolve the agent argv from ``GROKDECK_ACP_BIN`` or the grok CLI."""
    raw = os.environ.get("GROKDECK_ACP_BIN")
    if raw:
        parts = shlex.split(raw)
        if not parts:
            raise AcpError("missing_binary", "GROKDECK_ACP_BIN is empty")
        return parts
    # Lazy import to avoid circular at module load.
    from sidecar import cli_session

    bin_path = cli_session.grok_bin()
    if not bin_path:
        raise AcpError("missing_binary", "grok CLI not found")
    return [bin_path, "agent", "stdio"]


# ---- normalisation ---------------------------------------------------------


def _normalize_session(raw: Any, cwd_hint: Optional[str] = None) -> dict[str, Any]:
    """Public session shape: ``{id, title, cwd, updatedAt}``.

    Email, token, key, and bearer fields are never propagated. The title falls
    back to ``_meta.title`` and then to the cwd's folder name.
    """
    if not isinstance(raw, dict):
        raw = {}
    raw_id = raw.get("sessionId") or raw.get("id") or ""
    title = raw.get("title")
    if not title:
        meta = raw.get("_meta") or {}
        if isinstance(meta, dict):
            title = meta.get("title")
    cwd = raw.get("cwd") or cwd_hint or ""
    if not title and cwd:
        title = Path(str(cwd)).name or "Session"
    updated = raw.get("updatedAt")
    if not isinstance(updated, (int, float)):
        updated = 0
    return {
        "id": str(raw_id),
        "title": str(title) if title else "Session",
        "cwd": str(cwd),
        "updatedAt": updated,
    }


def _update_text(upd: dict[str, Any]) -> str:
    content = upd.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return ""


def _tool_payload(upd: dict[str, Any]) -> dict[str, Any]:
    """Coalesce a tool_call / tool_call_update into a public tool shape.

    ``_meta["x.ai/tool"]`` carries the useful bits (name, label, read_only);
    the top level carries title and locations. Nulls are preserved so the
    merge step can decide which fields to keep.
    """
    meta = (upd.get("_meta") or {}).get("x.ai/tool") or {}
    if not isinstance(meta, dict):
        meta = {}
    locations = upd.get("locations") or []
    first = locations[0] if locations and isinstance(locations[0], dict) else {}
    return {
        "id": str(upd.get("toolCallId") or ""),
        "name": str(meta.get("name") or upd.get("kind") or upd.get("title") or ""),
        "path": str(first.get("path", "") or ""),
        "readOnly": bool(meta.get("read_only")) if meta.get("read_only") is not None else False,
        "status": str(upd.get("status") or ""),
        "label": str(meta.get("label") or upd.get("title") or ""),
    }


# ---- session/update -> public events ---------------------------------------


def _event_from_update(params: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Translate one ``session/update`` notification into a public event.

    Returns ``None`` to drop the frame (e.g., empty ``agent_message_chunk``).
    """
    if not isinstance(params, dict):
        return None
    upd = params.get("update") or {}
    if not isinstance(upd, dict):
        upd = {}
    kind = upd.get("sessionUpdate")
    sid = params.get("sessionId") or ""

    if kind == "agent_message_chunk":
        text = _update_text(upd)
        if text:
            return {"type": "token", "sessionId": sid, "text": text}
        return None
    if kind == "agent_thought_chunk":
        text = _update_text(upd)
        if text:
            return {"type": "reasoning", "sessionId": sid, "text": text}
        return None
    if kind in ("tool_call", "tool_call_update"):
        return {"type": "tool", "sessionId": sid, **_tool_payload(upd)}
    if kind == "session_info_update":
        title = upd.get("title")
        if title:
            return {"type": "title", "sessionId": sid, "title": str(title)}
        return None
    if kind == "available_commands_update":
        commands = upd.get("availableCommands") or []
        return {"type": "command", "sessionId": sid, "commands": [
            {"name": command["name"], "description": str(command.get("description") or "")}
            for command in commands if isinstance(command, dict) and isinstance(command.get("name"), str)
        ]}
    return None


# ---- message folding for session/load --------------------------------------


def _build_messages(updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold a stream of update notifications into ordered messages.

    The same assistant message accumulates text, reasoning, and any tools that
    follow it. ``verbose`` joins tool lines and text chunks with newlines so
    the consumer can render a raw transcript.
    """
    msgs: list[dict[str, Any]] = []
    verbose_lines: list[list[str]] = []

    def _attach(role: str, key: str) -> int:
        # Keep a single message per (role, key) so tool_call_update merges
        # into the existing slot rather than spawning a new one.
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i]["role"] == role and msgs[i].get("_key") == key:
                return i
        new: dict[str, Any] = {
            "id": f"m{len(msgs)}",
            "role": role,
            "text": "",
            "reasoning": "",
            "tools": [],
            "verbose": "",
            "_key": key,
        }
        msgs.append(new)
        verbose_lines.append([])
        return len(msgs) - 1

    def _last_assistant() -> Optional[int]:
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i]["role"] == "assistant":
                return i
        return None

    for params in updates:
        if not isinstance(params, dict):
            continue
        upd = params.get("update") or {}
        if not isinstance(upd, dict):
            continue
        kind = upd.get("sessionUpdate")
        text = _update_text(upd)

        if kind == "user_message_chunk" and text:
            idx = _attach("user", "user-only")
            msgs[idx]["text"] = (msgs[idx]["text"] or "") + text
            verbose_lines[idx].append(text)
        elif kind == "agent_message_chunk" and text:
            idx = _attach("assistant", "assistant-only")
            msgs[idx]["text"] = msgs[idx]["text"] + text
            verbose_lines[idx].append(text)
        elif kind == "agent_thought_chunk" and text:
            idx = _attach("assistant", "assistant-only")
            msgs[idx]["reasoning"] = msgs[idx]["reasoning"] + text
            verbose_lines[idx].append(text)
        elif kind in ("tool_call", "tool_call_update"):
            tool = _tool_payload(upd)
            idx = _last_assistant()
            if idx is None:
                idx = _attach("assistant", "assistant-only")
            existing = next(
                (t for t in msgs[idx]["tools"] if t.get("id") == tool["id"]),
                None,
            )
            # Verbose line: include name, status, path so the transcript is
            # self-describing.
            verbose_line = (
                f"[tool] {tool['name']} status={tool['status']} path={tool['path']}"
            ).rstrip()
            if existing is not None and kind == "tool_call_update":
                # Merge: keep last non-null value per field.
                for k, v in tool.items():
                    if v in (None, "", [], {}):
                        continue
                    existing[k] = v
                verbose_lines[idx].append(verbose_line)
            else:
                msgs[idx]["tools"].append(tool)
                verbose_lines[idx].append(verbose_line)

    out: list[dict[str, Any]] = []
    for i, m in enumerate(msgs):
        out.append({
            "id": m["id"],
            "role": m["role"],
            "text": m["text"],
            "reasoning": m["reasoning"],
            "tools": m["tools"],
            "verbose": "\n".join(verbose_lines[i]),
        })
    return out


def _build_mode(value: str) -> str:
    mode = value or "ask"
    if mode not in BUILD_MODES:
        raise AcpError("invalid_mode", "Build mode must be Ask, Plan, Accept edits, or Full auto.")
    return mode


def _models_from_result(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    models = (result.get("models") or {}).get("availableModels") or []
    return [item for item in models if isinstance(item, dict)]


def _config_options_from_result(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        options = result.get("configOptions") or []
        return [item for item in options if isinstance(item, dict)]
    return []


def _config_value(options: list[dict[str, Any]], config_id: str) -> str:
    for option in options:
        if (option.get("id") or option.get("configId")) == config_id:
            return str(option.get("currentValue") or "")
    return ""


def _config_choices(options: list[dict[str, Any]], config_id: str) -> list[str]:
    for option in options:
        if (option.get("id") or option.get("configId")) == config_id:
            return [str(item["value"]) for item in option.get("options") or [] if isinstance(item, dict) and isinstance(item.get("value"), str)]
    return []


def _session_state(cwd: str, mode: str, result: Any) -> dict[str, Any]:
    model_state = result.get("models") if isinstance(result, dict) else {}
    model_state = model_state if isinstance(model_state, dict) else {}
    options = _config_options_from_result(result)
    return {
        "cwd": str(Path(cwd).expanduser().resolve()),
        "mode": _build_mode(mode),
        "models": _models_from_result(result),
        "configOptions": options,
        "model": str(model_state.get("currentModelId") or _config_value(options, "model")),
        "effort": _config_value(options, "reasoning_effort"),
    }


def _model_ids(models: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("modelId") or item.get("id") or "")
        for item in models
        if item.get("modelId") or item.get("id")
    }


def _public_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public: list[dict[str, Any]] = []
    for item in models:
        model_id = str(item.get("modelId") or item.get("id") or "")
        if not model_id:
            continue
        meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}
        efforts = meta.get("reasoningEfforts") or meta.get("reasoning_efforts") or []
        public.append({
            "id": model_id,
            "name": str(item.get("name") or item.get("displayName") or model_id),
            "_meta": {"reasoningEfforts": [str(value) for value in efforts if isinstance(value, str)]},
        })
    return public


def _reasoning_efforts(models: list[dict[str, Any]], model: str) -> set[str]:
    for item in models:
        if str(item.get("modelId") or item.get("id") or "") != model:
            continue
        meta = item.get("_meta") or {}
        efforts = meta.get("reasoningEfforts") or meta.get("reasoning_efforts") or []
        return {str(effort) for effort in efforts if isinstance(effort, str)}
    return set()


# ---- the agent -------------------------------------------------------------


class AcpAgent:
    """One long-lived ACP child. Public surface is per-call; state is internal."""

    def __init__(self, policy: Callable[[], dict[str, Any]] | None = None) -> None:
        self._policy = policy or (lambda: {})
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        # Per-request capture lists for session/load.
        self._capture: dict[int, list[dict[str, Any]]] = {}
        # The Grok CLI sends all session updates on one stdio stream. Keep
        # turns serial so updates and permission answers cannot cross sessions.
        self._stream_queue: Optional[asyncio.Queue] = None
        self._prompt_lock = asyncio.Lock()
        self._active_session: Optional[str] = None
        self._sessions: dict[str, dict[str, Any]] = {}
        self._approvals: dict[str, asyncio.Future[bool]] = {}
        self._approval_counter = 0
        self._terminals: dict[str, dict[str, Any]] = {}
        self._terminal_counter = 0
        # Parked session/request_permission frames waiting for a verdict.
        self._server_reqs: dict[int, dict[str, Any]] = {}
        self._reader_task: Optional[asyncio.Task] = None
        # Instructions keyed by session id. Popped on first prompt.
        self._instructions: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    # ---- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        async with self._lock:
            if self.proc is not None and self.proc.returncode is None:
                return
            argv = _argv_from_env()
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=STREAM_LIMIT,
                )
            except (OSError, FileNotFoundError) as exc:
                self.proc = None
                raise AcpError("spawn_failed", "could not start ACP agent") from exc

            self._reader_task = asyncio.create_task(self._read_stdout())
            try:
                await self._request("initialize", {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        # Grok delegates file and terminal work to this
                        # client. Each request is constrained to the session
                        # workspace and approved according to its build mode.
                        "fs": {"readTextFile": True, "writeTextFile": True},
                        "terminal": True,
                    },
                }, timeout=HANDSHAKE_TIMEOUT)
                await self._request("authenticate", {"methodId": "cached_token"},
                                    timeout=HANDSHAKE_TIMEOUT)
            except AcpError:
                await self._terminate()
                raise

    async def _terminate(self) -> None:
        await self._close_terminals()
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 2)
            except asyncio.TimeoutError:
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
        self.proc = None
        reader, self._reader_task = self._reader_task, None
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader

    async def release(self) -> None:
        """Stop the agent when no turn is running so regular chat can use grok."""
        if self._stream_queue is not None or self._sessions:
            return
        await self._terminate()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        task = self._reader_task
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._terminate()
        for fut in list(self._pending.values()):
            if not fut.done():
                try:
                    fut.set_exception(AcpError("closed", "agent closed"))
                except Exception:
                    pass
        self._pending.clear()
        for future in list(self._approvals.values()):
            if not future.done():
                future.set_result(False)
        self._approvals.clear()

    # ---- conversation ------------------------------------------------------

    async def list_sessions(self) -> list[dict[str, Any]]:
        await self.start()
        result = await self._request("session/list", {}, timeout=LIST_TIMEOUT)
        if isinstance(result, list):
            raw = result
        elif isinstance(result, dict):
            raw = result.get("sessions") or []
        else:
            raw = []
        sessions = [_normalize_session(item) for item in raw if isinstance(item, dict)]
        await self.release()
        return sessions

    async def open_session(self, cwd: str, instructions: str = "", mode: str = "ask") -> dict[str, Any]:
        """``session/new``. Stash ``instructions`` to prepend on first prompt."""
        await self.start()
        mode = _build_mode(mode)
        meta: dict[str, bool] = {}
        if mode == "full-auto":
            meta["yoloMode"] = True
        elif mode == "accept-edits":
            meta["autoMode"] = True
        captured: list[dict[str, Any]] = []
        result = await self._request("session/new", {
            "cwd": str(cwd),
            "mcpServers": [],
            "_meta": meta,
        }, timeout=NEW_TIMEOUT, capture_to=captured)
        session = _normalize_session(result or {}, cwd_hint=str(cwd))
        if not session["id"]:
            raise AcpError("no_session", "grok agent did not return a session ID")
        self._sessions[session["id"]] = _session_state(cwd, mode, result)
        if instructions:
            self._instructions[session["id"]] = instructions
        state = self._sessions[session["id"]]
        commands = [_event_from_update(item) for item in captured]
        return {**session, "models": _public_models(state["models"]),
                "model": state["model"], "effort": state["effort"],
                "efforts": _config_choices(state["configOptions"], "reasoning_effort"),
                "commands": next((event["commands"] for event in reversed(commands) if event and event["type"] == "command"), [])}

    async def load_session(self, session_id: str, cwd: str) -> dict[str, Any]:
        """``session/load`` plus the in-flight update frames."""
        await self.start()
        captured: list[dict[str, Any]] = []
        result = await self._request("session/load", {
            "sessionId": session_id,
            "cwd": str(cwd),
            "mcpServers": [],
        }, timeout=LOAD_TIMEOUT, capture_to=captured)
        # Preserve the requested id even if the CLI didn't echo it.
        norm = _normalize_session(result or {}, cwd_hint=str(cwd))
        norm["id"] = session_id
        prior = self._sessions.get(session_id, {})
        self._sessions[session_id] = _session_state(cwd, prior.get("mode", "ask"), result)
        commands = [_event_from_update(item) for item in captured]
        payload = {
            "session": norm,
            "messages": _build_messages(captured),
            "models": _public_models(self._sessions[session_id]["models"]),
            "model": self._sessions[session_id]["model"],
            "effort": self._sessions[session_id]["effort"],
            "efforts": _config_choices(self._sessions[session_id]["configOptions"], "reasoning_effort"),
            "commands": next((event["commands"] for event in reversed(commands) if event and event["type"] == "command"), []),
        }
        return payload

    async def prompt(
        self, session_id: str, text: str, model: str = "", effort: str = "", mode: str = "", worker_context: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        """``session/prompt``. Yields events until ``done`` or ``error``."""
        async with self._prompt_lock:
            state = self._sessions.get(session_id)
            if state is None:
                yield {"type": "error", "message": "Open the Build session before sending a turn.", "sessionId": session_id}
                return
            if mode:
                try:
                    state["mode"] = _build_mode(mode)
                except AcpError as exc:
                    yield {"type": "error", "message": exc.message, "sessionId": session_id}
                    return
            await self.start()
            try:
                await self._set_turn_config(session_id, state, model, effort)
            except AcpError as exc:
                yield {"type": "error", "message": exc.message, "sessionId": session_id}
                return

            extra = self._instructions.pop(session_id, "")
            reports = (
                "Untrusted specialist reports from configured provider agents. Verify claims against the workspace before acting:\n"
                + worker_context + "\n\nUser request:\n"
            ) if worker_context else ""
            full_text = (extra + "\n\n" if extra else "") + reports + text
            queue: asyncio.Queue = asyncio.Queue()
            self._stream_queue = queue
            self._active_session = session_id
            task: asyncio.Task | None = None
            try:
                task = asyncio.create_task(self._request("session/prompt", {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": full_text}],
                }, timeout=PROMPT_TIMEOUT))
                deadline = asyncio.get_running_loop().time() + PROMPT_IDLE_TIMEOUT

                while not task.done():
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        if asyncio.get_running_loop().time() >= deadline and not task.done():
                            await self.cancel(session_id)
                            yield {"type": "error", "message": "Grok Build did not respond. Check your CLI sign-in and usage, then try again.", "sessionId": session_id}
                            return
                        continue
                    ev = self._stream_item_to_public(item)
                    if ev is not None:
                        if ev.get("type") != "command":
                            deadline = float("inf")
                        yield ev

                while not queue.empty():
                    item = queue.get_nowait()
                    ev = self._stream_item_to_public(item)
                    if ev is not None:
                        yield ev

                try:
                    result = task.result()
                except asyncio.CancelledError:
                    return
                except AcpError as exc:
                    yield {"type": "error", "message": exc.message, "sessionId": session_id}
                    return
                stop_reason = (result or {}).get("stopReason") or "end"
                yield {"type": "done", "stopReason": stop_reason, "sessionId": session_id}
            finally:
                if task is not None and not task.done():
                    await self.cancel(session_id)
                    try:
                        await asyncio.wait_for(task, 2)
                    except (asyncio.TimeoutError, asyncio.CancelledError, AcpError):
                        await self._terminate()
                        self._sessions.clear()
                self._active_session = None
                self._stream_queue = None

    async def _set_turn_config(
        self, session_id: str, state: dict[str, Any], model: str, effort: str,
    ) -> None:
        """Apply only documented ACP options and surface unsupported choices."""
        models = state.get("models") or []
        if model:
            available = _model_ids(models)
            if available and model not in available:
                raise AcpError("invalid_model", f"{model} is not available in this Grok session.")
            if state.get("model") != model:
                result = await self._request("session/set_config_option", {
                    "sessionId": session_id,
                    "configId": "model",
                    "value": model,
                }, timeout=NEW_TIMEOUT)
                options = _config_options_from_result(result)
                if options:
                    state["configOptions"] = options
                state["model"] = model
        if effort:
            chosen_model = model or self._configured_model(state)
            supported = _reasoning_efforts(models, chosen_model) if chosen_model else set()
            supported |= set(_config_choices(state.get("configOptions") or [], "reasoning_effort"))
            if supported and effort not in supported:
                raise AcpError("invalid_effort", f"{effort} is not available for {chosen_model}.")
            if state.get("effort") != effort:
                result = await self._request("session/set_config_option", {
                    "sessionId": session_id,
                    "configId": "reasoning_effort",
                    "value": effort,
                }, timeout=NEW_TIMEOUT)
                options = _config_options_from_result(result)
                if options:
                    state["configOptions"] = options
                state["effort"] = effort

    @staticmethod
    def _configured_model(state: dict[str, Any]) -> str:
        if state.get("model"):
            return str(state["model"])
        for option in state.get("configOptions") or []:
            if option.get("id") != "model" and option.get("configId") != "model":
                continue
            value = option.get("value") or {}
            if isinstance(value, dict) and value.get("value"):
                return str(value["value"])
        models = state.get("models") or []
        return str(models[0].get("modelId") or models[0].get("id") or "") if models else ""

    async def _close_terminals(self) -> None:
        terminals, self._terminals = self._terminals, {}
        for slot in terminals.values():
            proc = slot.get("proc")
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            reader = slot.get("reader")
            if reader is not None:
                reader.cancel()
        for slot in terminals.values():
            proc = slot.get("proc")
            if proc is not None:
                with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(proc.wait(), timeout=1)

    @staticmethod
    def _workspace_path(state: dict[str, Any], raw: Any) -> Optional[Path]:
        if not isinstance(raw, str) or not raw:
            return None
        root = Path(str(state["cwd"])).resolve()
        path = Path(raw).expanduser()
        candidate = path if path.is_absolute() else root / path
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            return None
        return resolved

    async def _approval(self, state: dict[str, Any], payload: dict[str, Any]) -> bool:
        """Enforce Build modes on delegated file and terminal requests."""
        operation = payload.get("operation")
        policy = self._policy()
        if operation == "terminal" and not policy.get("allowTerminal", True):
            return False
        if operation in {"read", "write"} and not policy.get("allowFs", True):
            return False
        if operation == "read":
            return True
        mode = state["mode"]
        if mode == "plan":
            return False
        if policy.get("autoApprove") or mode == "full-auto" or (mode == "accept-edits" and operation == "write"):
            return True
        if self._stream_queue is None:
            return False
        self._approval_counter += 1
        approval_id = f"local-{self._approval_counter}"
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._approvals[approval_id] = future
        try:
            self._stream_queue.put_nowait({
                "type": "permission",
                "id": approval_id,
                "params": {"detail": payload.get("detail") or "Approve this action?"},
            })
            return await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT)
        except (asyncio.QueueFull, asyncio.TimeoutError):
            return False
        finally:
            self._approvals.pop(approval_id, None)

    async def _capture_terminal(self, stream: asyncio.StreamReader, output: bytearray) -> None:
        while chunk := await stream.read(32 * 1024):
            remaining = MAX_TERMINAL_OUTPUT - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])

    async def _handle_fs_request(
        self, method: str, request_id: Any, params: dict[str, Any], state: dict[str, Any],
    ) -> None:
        reading = method == "fs/read_text_file"
        path = self._workspace_path(state, params.get("path"))
        if path is None:
            await self._send_error(request_id, "path is outside the Build workspace")
            return
        if reading:
            if not await self._approval(state, {"operation": "read"}):
                await self._send_error(request_id, "file reads are disabled in Settings")
                return
            if not path.is_file():
                await self._send_error(request_id, f"cannot read {path.name}")
                return
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    await self._send_error(request_id, f"{path.name} is too large to read")
                    return
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                await self._send_error(request_id, f"cannot read {path.name}")
                return
            await self._send({"jsonrpc": "2.0", "id": request_id, "result": {"content": content}})
            return
        content = params.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
            await self._send_error(request_id, "file content is invalid or too large")
            return
        if not await self._approval(state, {"operation": "write", "detail": f"Write {path.relative_to(state['cwd'])}"}):
            await self._send_error(request_id, "the user denied writing this file")
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError:
            await self._send_error(request_id, f"cannot write {path.name}")
            return
        await self._send({"jsonrpc": "2.0", "id": request_id, "result": {}})

    async def _handle_terminal_request(
        self, method: str, request_id: Any, params: dict[str, Any], state: dict[str, Any],
    ) -> None:
        if method == "terminal/create":
            command = params.get("command")
            args = params.get("args") or []
            if not isinstance(command, str) or not command or not isinstance(args, list):
                await self._send_error(request_id, "terminal command is invalid")
                return
            argv = [command, *[str(arg) for arg in args]]
            if not await self._approval(state, {"operation": "terminal", "detail": "Run " + " ".join(argv)}):
                await self._send_error(request_id, "the user denied running this command")
                return
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=state["cwd"],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            except OSError:
                await self._send_error(request_id, f"cannot start {command}")
                return
            self._terminal_counter += 1
            terminal_id = f"terminal-{self._terminal_counter}"
            output = bytearray()
            reader = asyncio.create_task(self._capture_terminal(proc.stdout, output)) if proc.stdout else None
            self._terminals[terminal_id] = {"proc": proc, "output": output, "reader": reader}
            await self._send({"jsonrpc": "2.0", "id": request_id, "result": {"terminalId": terminal_id}})
            return

        terminal_id = str(params.get("terminalId") or "")
        slot = self._terminals.get(terminal_id)
        if slot is None:
            await self._send_error(request_id, "terminal is no longer available")
            return
        proc = slot["proc"]
        if method == "terminal/kill" and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        if method == "terminal/wait_for_exit":
            await proc.wait()
            reader = slot.get("reader")
            if reader is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await reader
        output = bytes(slot["output"]).decode("utf-8", "replace")
        result: dict[str, Any] = {
            "output": output,
            "exitCode": proc.returncode,
            "truncated": len(slot["output"]) >= MAX_TERMINAL_OUTPUT,
        }
        if method == "terminal/release":
            self._terminals.pop(terminal_id, None)
            result = {}
        await self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def _send_error(self, request_id: Any, message: str) -> None:
        await self._send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": message}})

    async def cancel(self, session_id: str) -> None:
        """``session/cancel`` notification. Fire-and-forget."""
        if self.proc is None or self.proc.returncode is not None:
            return
        try:
            await self._send({
                "jsonrpc": "2.0",
                "method": "session/cancel",
                "params": {"sessionId": session_id},
            })
        except AcpError:
            pass

    async def answer_permission(self, request_id: Any, allow: bool) -> None:
        """Reply to a parked ``session/request_permission`` frame."""
        local_id = str(request_id or "")
        local = self._approvals.get(local_id)
        if local is not None:
            if not local.done():
                local.set_result(bool(allow))
            return
        try:
            rid = int(request_id)
        except (TypeError, ValueError):
            return
        info = self._server_reqs.pop(rid, None)
        if info is None:
            return
        params = info.get("params") or {}
        options = params.get("options") or []
        chosen_kind = "allow_once" if allow else "reject_once"
        chosen = next(
            (o for o in options if isinstance(o, dict) and o.get("kind") == chosen_kind),
            None,
        )
        try:
            if chosen is not None and isinstance(chosen, dict):
                await self._send({
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {"outcome": {"outcome": "selected",
                                           "optionId": chosen["optionId"]}},
                })
            else:
                await self._send({
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {"outcome": {"outcome": "cancelled"}},
                })
        except AcpError:
            pass

    # ---- transport ---------------------------------------------------------

    def _stream_item_to_public(self, item: Any) -> Optional[dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        kind = item.get("type")
        if kind == "update":
            return _event_from_update(item.get("params") or {})
        if kind == "permission":
            params = item.get("params") or {}
            return {
                "type": "permission",
                "id": item.get("id"),
                "sessionId": params.get("sessionId"),
                "toolCall": params.get("toolCall"),
                "options": params.get("options"),
                "detail": params.get("detail") or "Approve this action?",
            }
        return None

    async def _send(self, obj: dict[str, Any]) -> None:
        if not self.proc or self.proc.returncode is not None:
            raise AcpError("agent_down", "ACP agent is not running")
        assert self.proc.stdin
        line = (json.dumps(obj) + "\n").encode()
        try:
            self.proc.stdin.write(line)
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise AcpError("agent_down", "ACP agent is not running") from exc

    async def _request(self, method: str, params: Optional[dict[str, Any]] = None,
                      timeout: float = 60.0, *,
                      capture_to: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.Future()
        self._pending[rid] = fut
        if capture_to is not None:
            self._capture[rid] = capture_to
        try:
            await self._send({
                "jsonrpc": "2.0",
                "id": rid,
                "method": method,
                "params": params or {},
            })
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise AcpError("timeout", f"{method} timed out") from None
        finally:
            if capture_to is not None:
                self._capture.pop(rid, None)
            self._pending.pop(rid, None)

    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    return
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                try:
                    await self._route(msg)
                except Exception:
                    # Routing errors must never kill the reader.
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _route(self, msg: dict[str, Any]) -> None:
        if not isinstance(msg, dict):
            return

        # Response to one of our requests.
        if "id" in msg and ("result" in msg or "error" in msg):
            rid = msg["id"]
            fut = self._pending.pop(rid, None)
            if fut is not None and not fut.done():
                if "error" in msg:
                    err = msg["error"] or {}
                    try:
                        fut.set_exception(AcpError(
                            str(err.get("code", "rpc")),
                            str(err.get("message", "agent error"))))
                    except Exception:
                        pass
                else:
                    try:
                        fut.set_result(msg.get("result") or {})
                    except Exception:
                        pass
            return

        # Server -> client request.
        if "id" in msg and "method" in msg:
            method = msg.get("method")
            params = msg.get("params") or {}
            if method == "session/request_permission":
                self._server_reqs[msg["id"]] = {"params": params}
                if self._stream_queue is not None:
                    try:
                        self._stream_queue.put_nowait({
                            "type": "permission",
                            "id": msg["id"],
                            "params": params,
                        })
                    except asyncio.QueueFull:
                        pass
                return
            session_id = str(params.get("sessionId") or self._active_session or "")
            state = self._sessions.get(session_id)
            if method in {"fs/read_text_file", "fs/write_text_file"}:
                if state is None:
                    await self._send_error(msg["id"], "Build session is not active")
                else:
                    await self._handle_fs_request(method, msg["id"], params, state)
                return
            if method in {"terminal/create", "terminal/output", "terminal/wait_for_exit", "terminal/kill", "terminal/release"}:
                if state is None:
                    await self._send_error(msg["id"], "Build session is not active")
                else:
                    await self._handle_terminal_request(method, msg["id"], params, state)
                return
            # Answer unimplemented server requests so the agent never stalls.
            try:
                await self._send({
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "error": {"code": -32601,
                              "message": f"grokdeck does not implement {method}"},
                })
            except AcpError:
                pass
            return

        # Notifications we care about: session/update.
        if msg.get("method") == "session/update":
            params = msg.get("params") or {}
            # Capture for any active session/load request.
            for cap in list(self._capture.values()):
                if isinstance(cap, list):
                    cap.append(params)
            # Forward to the active prompt consumer.
            if self._stream_queue is not None:
                try:
                    self._stream_queue.put_nowait({"type": "update", "params": params})
                except asyncio.QueueFull:
                    pass
