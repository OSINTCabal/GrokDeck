"""GrokDeck bridge to the Grok Build agent over ACP.

Spawns ``grok agent stdio`` (the Agent Client Protocol server shipped with the
Grok CLI) and speaks ACP to it. One process is cwd-locked to one session, the
same way Mistral's app-server is.

Wire contract — verified against **grok 1.0.52** by live probe; see
``docs/ACP_CONTRACT.md`` for the raw recordings this file is written against.

  * JSON-RPC 2.0, newline-delimited, on stdin/stdout. stderr is human logs.
  * Handshake: ``initialize`` → ``authenticate`` → ``session/new``.
  * ``session/prompt`` blocks for the whole turn and returns ``stopReason``
    (``end_turn`` | ``cancelled`` | ``max_turn_requests`` | ``refusal``).
  * Live output arrives as ``session/update`` notifications carrying an
    ``update.sessionUpdate`` discriminator.
  * ``session/cancel`` is a *notification*; the in-flight ``session/prompt``
    resolves with ``stopReason: "cancelled"``.
  * The agent can call *us*: ``session/request_permission``, ``fs/read_text_file``.
    Every server request must be answered or the turn stalls.
  * Token + cost accounting rides on the prompt result's ``_meta.usage``.

Ops notes
  * Credentials are never read here. The child inherits the user's own
    ``~/.grok/auth.json``; this module only asks it to use the cached token.
  * Nothing is logged that contains conversation content.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

# Observed stopReason values.
STOP_END_TURN = "end_turn"
STOP_CANCELLED = "cancelled"
STOP_MAX_TURNS = "max_turn_requests"
STOP_REFUSAL = "refusal"

# Observed sessionUpdate discriminators.
U_MESSAGE = "agent_message_chunk"
U_USER_MESSAGE = "user_message_chunk"
U_THOUGHT = "agent_thought_chunk"
U_TOOL_CALL = "tool_call"
U_TOOL_UPDATE = "tool_call_update"
U_PLAN = "plan"
U_SESSION_INFO = "session_info_update"
U_COMMANDS = "available_commands_update"
U_MODE = "current_mode_update"

# Server -> client method groups. Observed live on grok 1.0.52: the agent
# delegates fs and terminal access instead of asking for permission.
_FS_METHODS = {"fs/read_text_file", "fs/write_text_file"}
_TERMINAL_METHODS = {
    "terminal/create", "terminal/output", "terminal/wait_for_exit",
    "terminal/kill", "terminal/release",
}

DEFAULT_TIMEOUT = 600.0
INITIALIZE_TIMEOUT = 60.0

# Max bytes for one newline-delimited ACP frame. The asyncio default (64 KiB)
# is too small in practice — see the note at the create_subprocess_exec call.
STREAM_LIMIT = 16 * 1024 * 1024

Logger = Callable[[str, dict], None]


class BridgeError(RuntimeError):
    """A protocol-level failure worth surfacing to the UI verbatim."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class PermissionRequest:
    """One outstanding ``session/request_permission`` awaiting a UI decision."""

    __slots__ = ("id", "session_id", "tool_call", "options", "received_at", "future")

    def __init__(self, request_id: int, session_id: str, params: dict):
        self.id = request_id
        self.session_id = session_id
        self.tool_call = params.get("toolCall") or {}
        self.options = params.get("options") or []
        self.received_at = time.time()
        self.future: asyncio.Future = asyncio.get_event_loop().create_future()

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "toolCall": self.tool_call,
            "options": self.options,
            "receivedAt": self.received_at,
        }

    def resolve(self, option_id: Optional[str]) -> None:
        if self.future.done():
            return
        if option_id:
            self.future.set_result({"outcome": {"outcome": "selected", "optionId": option_id}})
        else:
            self.future.set_result({"outcome": {"outcome": "cancelled"}})


class GrokAgent:
    """One ``grok agent stdio`` process bound to one working directory."""

    def __init__(
        self,
        cwd: Path,
        session_id: str,
        *,
        bin_path: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        log: Optional[Logger] = None,
        approval_policy: Callable[[dict], Awaitable[Optional[str]]] = None,
        needs_approval: Callable[[dict], Awaitable[bool]] = None,
        fs_read: Optional[Callable[[Path], Awaitable[Optional[str]]]] = None,
        fs_write: Optional[Callable[[Path, str], Awaitable[bool]]] = None,
        terminal: Optional[Callable[[dict], Awaitable[dict]]] = None,
        on_title: Optional[Callable[[str], None]] = None,
        allow_fs: bool = True,
    ):
        self.cwd = Path(cwd).expanduser().resolve()
        self.session_id = session_id
        self.model = model
        self.reasoning_effort = reasoning_effort
        self._log = log or (lambda _e, _d: None)
        self._approval_policy = approval_policy
        self._needs_approval = needs_approval
        self._fs_read = fs_read
        self._fs_write = fs_write
        self._terminal = terminal
        self._on_title = on_title
        self._allow_fs = allow_fs

        self.proc: Optional[asyncio.subprocess.Process] = None
        self.acp_session_id: Optional[str] = None
        self.models: list[dict] = []
        self.current_model: Optional[str] = None
        self.commands: list[dict] = []
        self.account: dict = {}
        self.agent_version: Optional[str] = None

        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._permissions: dict[int, PermissionRequest] = {}
        self.subscribers: set[asyncio.Queue] = set()
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._replay_target: Optional[asyncio.Queue] = None
        self._closed = False
        self.turn_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()

    # ---- lifecycle -------------------------------------------------------
    def find_binary(self) -> str:
        override = os.environ.get("GROKDECK_GROK_BIN")
        if override:
            return override
        found = shutil.which("grok")
        if found:
            return found
        candidate = Path.home() / ".local/bin/grok"
        if candidate.exists():
            return str(candidate)
        raise BridgeError("missing_binary", "grok CLI not found. Install with: "
                                           "curl -fsSL https://x.ai/cli/install.sh | bash")

    async def start(self, resume_session: Optional[str] = None) -> None:
        """Boot the agent.

        ``resume_session`` reattaches to an existing ACP session id instead of
        creating a new one — without it, a restart orphans the conversation.
        """
        if self.proc is not None:
            return
        binary = self.find_binary()
        argv = [binary, "agent"]
        if self.model:
            argv += ["-m", self.model]
        if self.reasoning_effort:
            argv += ["--reasoning-effort", self.reasoning_effort]
        # --no-leader keeps each session's agent in its own process so a
        # crash or a stray leader cannot couple unrelated conversations.
        argv += ["--no-leader", "stdio"]

        self.proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.cwd),
            env={**os.environ, "NO_COLOR": "1"},
            # asyncio's default stream limit is 64 KiB. ACP frames are
            # newline-delimited JSON and routinely exceed that — an
            # available_commands_update carries the whole command catalog, and
            # tool results can be large. Overrunning raises LimitOverrunError
            # and kills the reader mid-turn, so raise the ceiling generously.
            limit=STREAM_LIMIT,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

        await self._handshake(resume=resume_session)

    async def _handshake(self, resume: Optional[str] = None) -> None:
        init = await self.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                # Only advertise what we actually implement. Advertising fs
                # without honouring it would let the agent read the disk
                # behind the user's back.
                "fs": {"readTextFile": self._allow_fs and self._fs_read is not None,
                       "writeTextFile": self._allow_fs and self._fs_write is not None},
                "terminal": self._terminal is not None,
            },
        }, timeout=INITIALIZE_TIMEOUT)

        caps = (init.get("agentCapabilities") or {})
        self.agent_version = (init.get("_meta") or {}).get("agentVersion")

        # Prefer the token the CLI already holds; never open a browser here.
        auth = await self.request("authenticate", {"methodId": "cached_token"},
                                  timeout=INITIALIZE_TIMEOUT)
        self.account = auth.get("_meta") or {}

        result: dict = {}
        if resume:
            # Reattach to a session the CLI already owns. Creating a fresh
            # one here would silently orphan the stored conversation and make
            # every existing chat open empty after a restart.
            try:
                await self.request("session/load", {
                    "sessionId": resume,
                    "cwd": str(self.cwd),
                    "mcpServers": [],
                }, timeout=120)
                self.acp_session_id = resume
                result = {"sessionId": resume, "resumed": True}
            except BridgeError as exc:
                self._log("resume_failed", {"session": resume, "error": exc.message})
                result = {}

        if not result:
            result = await self.request("session/new", {
                "cwd": str(self.cwd),
                "mcpServers": [],
            }, timeout=90)
        self.acp_session_id = result.get("sessionId")
        if not self.acp_session_id:
            raise BridgeError("no_session", "grok agent did not return a sessionId")

        # The model catalog only comes back from session/new, so ask for it
        # explicitly rather than depending on which branch we took.
        model_state = result.get("models") or {}
        if not model_state.get("availableModels"):
            try:
                catalog = await self.request("session/new", {
                    "cwd": str(self.cwd), "mcpServers": [],
                    "ephemeral": True,
                }, timeout=60)
                model_state = catalog.get("models") or {}
                if catalog.get("sessionId") and catalog["sessionId"] != self.acp_session_id:
                    await self.request("session/close",
                                       {"sessionId": catalog["sessionId"]}, timeout=20)
            except BridgeError:
                pass
        self.models = model_state.get("availableModels") or []
        self.current_model = model_state.get("currentModelId")
        self._log("bridge_started", {
            "acp_session": self.acp_session_id,
            "version": self.agent_version,
            "models": len(self.models),
            "prompt_image": (caps.get("promptCapabilities") or {}).get("image"),
        })

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in (self._reader_task, self._stderr_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
        # Never leave a caller hanging on an unanswered future.
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(BridgeError("closed", "agent closed"))
        self._pending.clear()
        for req in list(self._permissions.values()):
            req.resolve(None)
        self._permissions.clear()
        for q in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait({"type": "closed"})
        self.subscribers.clear()

    # ---- transport -------------------------------------------------------
    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while line := await self.proc.stdout.readline():
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    self._log("non_json_stdout", {"line": text[:200]})
                    continue
                try:
                    await self._route(msg)
                except Exception as exc:  # noqa: BLE001
                    self._log("route_error", {"error": repr(exc), "method": msg.get("method")})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._log("stdout_reader_died", {"error": repr(exc)})

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        try:
            while line := await self.proc.stderr.readline():
                self._log("agent_stderr", {"line": line.decode("utf-8", "replace")[:400]})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    async def _route(self, msg: dict) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(BridgeError(
                        str(err.get("code", "rpc")), str(err.get("message", "agent error"))))
                else:
                    fut.set_result(msg.get("result") or {})
            return

        if "id" in msg and "method" in msg:
            await self._handle_server_request(msg)
            return

        if msg.get("method") == "session/update":
            await self._handle_update(msg.get("params") or {})
            return
        # Unknown notification: ignore rather than crash the reader.

    async def _handle_server_request(self, msg: dict) -> None:
        """The agent calls *back* into the client for capabilities.

        Observed against grok 1.0.52: rather than asking for permission, the
        agent simply delegates — it issues ``fs/read_text_file`` and
        ``terminal/create`` because we advertised those capabilities in
        ``initialize``. ``session/request_permission`` was never observed.

        That makes **this client the enforcement point**: anything we decline
        simply fails, and the agent adapts. GrokDeck gates writes and terminal
        commands through its own approval UI; reads are allowed by default.
        """
        method, rid = msg.get("method"), msg["id"]
        params = msg.get("params") or {}

        if method == "session/request_permission":
            # ACP-standard path. Kept even though grok 1.0.52 did not use it,
            # so a future build that does ask is already handled.
            req = PermissionRequest(rid, params.get("sessionId") or "", params)
            self._permissions[rid] = req
            await self.emit({"type": "permission_request", **req.to_json()})
            try:
                choice = await self._approval_policy(req.to_json()) if self._approval_policy else None
            finally:
                self._permissions.pop(rid, None)
            req.resolve(choice)
            await self._send({"jsonrpc": "2.0", "id": rid, "result": await req.future})
            await self.emit({"type": "permission_resolved", "id": rid, "optionId": choice})
            return

        if method in _FS_METHODS:
            await self._handle_fs(method, rid, params)
            return

        if method in _TERMINAL_METHODS:
            await self._handle_terminal(method, rid, params)
            return

        # Answering method-not-found is mandatory: silence stalls the turn.
        await self._send({"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32601, "message": f"grokdeck does not implement {method}"}})

    async def _handle_fs(self, method: str, rid: int, params: dict) -> None:
        raw = params.get("path") or ""
        path = Path(raw).expanduser()
        reading = method == "fs/read_text_file"

        if not self._allow_fs:
            await self._send({"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32601, "message": "filesystem access is disabled in GrokDeck"}})
            return

        if self._needs_approval is not None:
            verdict = await self._needs_approval({
                "kind": "fs", "op": "read" if reading else "write",
                "path": raw,
                "detail": (params.get("content") or "")[:400] if not reading else "",
            })
            if not verdict:
                await self.emit({"type": "denied", "op": "fs",
                                 "path": raw, "read": reading})
                await self._send({"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32000,
                    "message": f"the user denied {'reading' if reading else 'writing'} {raw}"}})
                return

        if reading:
            if self._fs_read is None:
                await self._send({"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32601, "message": "fs not enabled"}})
                return
            content = await self._fs_read(path)
            if content is None:
                await self._send({"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32000, "message": f"cannot read {path}"}})
            else:
                await self._send({"jsonrpc": "2.0", "id": rid, "result": {"content": content}})
            return

        if self._fs_write is None:
            await self._send({"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32601, "message": "fs not enabled"}})
            return
        ok = await self._fs_write(path, params.get("content") or "")
        if ok:
            await self.emit({"type": "wrote", "path": raw})
            await self._send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            await self._send({"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32000, "message": f"cannot write {path}"}})

    async def _handle_terminal(self, method: str, rid: int, params: dict) -> None:
        """Terminal access.

        We advertise ``terminal: true`` only when a handler is installed.
        Declining is always safe: the agent reports the command could not run.
        """
        if not self._terminal:
            await self._send({"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32601, "message": "terminal access is disabled in GrokDeck"}})
            return

        if method == "terminal/create":
            command = params.get("command") or ""
            args = params.get("args") or []
            argv = " ".join([command, *args]).strip()
            if self._needs_approval is not None:
                verdict = await self._needs_approval({
                    "kind": "terminal", "op": "create", "command": command,
                    "args": args, "detail": argv,
                })
                if not verdict:
                    await self.emit({"type": "denied", "op": "terminal", "detail": argv})
                    await self._send({"jsonrpc": "2.0", "id": rid, "error": {
                        "code": -32000,
                        "message": f"the user denied running `{argv}`"}})
                    return
            term = await self._terminal({"op": "create", "command": command,
                                         "args": args, "cwd": self.cwd})
            await self._send({"jsonrpc": "2.0", "id": rid, "result": term})
            return

        if method == "terminal/output":
            out = await self._terminal({"op": "output",
                                        "terminalId": params.get("terminalId")})
            await self._send({"jsonrpc": "2.0", "id": rid, "result": out})
            return

        if method == "terminal/wait_for_exit":
            out = await self._terminal({"op": "wait",
                                        "terminalId": params.get("terminalId")})
            await self._send({"jsonrpc": "2.0", "id": rid, "result": out})
            return

        if method == "terminal/kill":
            out = await self._terminal({"op": "kill",
                                        "terminalId": params.get("terminalId")})
            await self._send({"jsonrpc": "2.0", "id": rid, "result": out})
            return

        if method == "terminal/release":
            await self._terminal({"op": "release", "terminalId": params.get("terminalId")})
            await self._send({"jsonrpc": "2.0", "id": rid, "result": {}})
            return

        await self._send({"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32601, "message": f"unsupported terminal method {method}"}})

    async def _handle_update(self, params: dict) -> None:
        upd = params.get("update") or {}
        kind = upd.get("sessionUpdate")

        if kind == U_SESSION_INFO and upd.get("title"):
            # The agent titles the session for us — persist it rather than
            # spending a model call on titles.
            if self._on_title is not None:
                try:
                    self._on_title(upd["title"])
                except Exception:  # noqa: BLE001
                    self._log("title_persist_failed", {"title": upd["title"]})
            await self.emit({"type": "title", "title": upd["title"]})
            return
        if kind == U_COMMANDS:
            self.commands = upd.get("availableCommands") or []
            await self.emit({"type": "commands", "commands": self.commands})
            return
        if kind == U_MODE:
            await self.emit({"type": "mode", "modeId": upd.get("currentModeId")})
            return
        if kind == U_MESSAGE:
            content = upd.get("content") or {}
            if content.get("text"):
                await self.emit({"type": "text", "delta": content["text"]})
            return
        if kind == U_USER_MESSAGE:
            content = upd.get("content") or {}
            if content.get("text"):
                await self.emit({"type": "user_text", "text": content["text"]})
            return
        if kind == U_THOUGHT:
            content = upd.get("content") or {}
            if content.get("text"):
                await self.emit({"type": "thought", "delta": content["text"]})
            return
        if kind == U_TOOL_CALL:
            await self.emit({"type": "tool_call", **_tool_payload(upd)})
            return
        if kind == U_TOOL_UPDATE:
            await self.emit({"type": "tool_update", **_tool_payload(upd)})
            return
        if kind == U_PLAN:
            await self.emit({"type": "plan", "entries": upd.get("entries") or []})
            return
        await self.emit({"type": "update", "update": upd})

    async def _send(self, obj: dict) -> None:
        if not self.proc or self.proc.returncode is not None:
            raise BridgeError("agent_down", "grok agent is not running")
        assert self.proc.stdin
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        with contextlib.suppress(ConnectionResetError):
            await self.proc.stdin.drain()

    async def request(self, method: str, params: Optional[dict] = None,
                      timeout: float = DEFAULT_TIMEOUT) -> dict:
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                              "params": params or {}})
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise BridgeError("timeout", f"{method} timed out after {timeout}s") from None
        finally:
            self._pending.pop(rid, None)

    def notify(self, method: str, params: Optional[dict] = None) -> None:
        asyncio.create_task(self._send(
            {"jsonrpc": "2.0", "method": method, "params": params or {}}))

    # ---- conversation ----------------------------------------------------
    async def prompt(self, text: str, request_id: Optional[str] = None) -> dict:
        """Send one turn. Blocks until stopReason; streams via subscribers."""
        async with self.turn_lock:
            await self.emit({"type": "turn_started", "requestId": request_id})
            try:
                result = await self.request("session/prompt", {
                    "sessionId": self.acp_session_id,
                    "prompt": [{"type": "text", "text": text}],
                })
            except BridgeError as exc:
                await self.emit({"type": "error", "message": exc.message,
                                 "code": exc.code})
                raise
            meta = result.get("_meta") or {}
            await self.emit({
                "type": "turn_completed",
                "stopReason": result.get("stopReason"),
                "requestId": meta.get("requestId"),
                "usage": meta.get("usage") or {},
                "modelId": meta.get("modelId"),
                "totalTokens": meta.get("totalTokens"),
            })
            return {"stopReason": result.get("stopReason"), "meta": meta}

    async def cancel(self) -> None:
        """Cancel the in-flight turn. The pending prompt resolves as cancelled."""
        self.notify("session/cancel", {"sessionId": self.acp_session_id})

    async def replay(self) -> list[dict]:
        """Reload this session's history into the subscriber stream."""
        if not self.acp_session_id:
            return []
        try:
            await self.request("session/load", {
                "sessionId": self.acp_session_id,
                "cwd": str(self.cwd),
                "mcpServers": [],
            }, timeout=120)
        except BridgeError as exc:
            self._log("replay_failed", {"error": exc.message})
            return []
        await self.emit({"type": "replayed"})
        return []

    async def set_mode(self, mode_id: str) -> None:
        # Observed: grok accepts any modeId without validating it, so this is
        # advisory. We record what the user asked for and let the agent decide.
        try:
            await self.request("session/set_mode",
                               {"sessionId": self.acp_session_id, "modeId": mode_id},
                               timeout=20)
        except BridgeError:
            pass
        await self.emit({"type": "mode", "modeId": mode_id})

    # ---- subscribers -----------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=4096)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    async def emit(self, event: dict) -> None:
        # While a targeted replay is in flight, frames go only to that
        # subscriber — otherwise session/load would duplicate history into
        # every other live listener.
        if self._replay_target is not None:
            try:
                self._replay_target.put_nowait(event)
            except asyncio.QueueFull:
                self._replay_target = None
            return
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled consumer must never block the agent. Drop the
                # subscriber; the client can re-subscribe and replay.
                self.subscribers.discard(q)
                self._log("subscriber_dropped", {})

    async def replay_for(self, q: asyncio.Queue) -> bool:
        """Load this session's history into a single fresh subscriber.

        ACP has no replay buffer, so opening a session must ask the agent to
        re-send. Scoped to ``q`` so existing live listeners don't get a second
        copy of the conversation.
        """
        if not self.acp_session_id:
            return False
        self._replay_target = q
        try:
            await self.request("session/load", {
                "sessionId": self.acp_session_id,
                "cwd": str(self.cwd),
                "mcpServers": [],
            }, timeout=180)
            return True
        except BridgeError as exc:
            self._log("replay_failed", {"error": exc.message})
            return False
        finally:
            self._replay_target = None


def _tool_payload(upd: dict) -> dict:
    """Normalize the observed tool_call / tool_call_update frames.

    ``_meta["x.ai/tool"]`` carries the useful bits (kind, label, read_only,
    namespace); the top level carries title and locations.
    """
    tool_meta = (upd.get("_meta") or {}).get("x.ai/tool") or {}
    return {
        "toolCallId": upd.get("toolCallId"),
        "title": upd.get("title"),
        "kind": upd.get("kind") or tool_meta.get("kind"),
        "label": tool_meta.get("label"),
        "name": tool_meta.get("name"),
        "namespace": tool_meta.get("namespace"),
        "readOnly": tool_meta.get("read_only"),
        "status": upd.get("status"),
        "locations": upd.get("locations") or [],
        "rawInput": upd.get("rawInput") or {},
        "content": upd.get("content") or [],
    }


def find_grok() -> str:
    for candidate in (os.environ.get("GROKDECK_GROK_BIN"), shutil.which("grok"),
                      str(Path.home() / ".local/bin/grok")):
        if candidate and Path(candidate).exists():
            return candidate
    raise BridgeError("missing_binary", "grok CLI not found")