"""GrokDeck HTTP API.

Chat streams through the xAI API when a key is saved. With no key, a signed-in
Grok CLI session answers the turn. Build mode can optionally drive that CLI
over ACP. Browser sign-in is `grok login --oauth`. The API key and the CLI
credential file never appear in a response body or a log.
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from sidecar import cli_session, providers, secrets, usage, voice, xai
from sidecar.acp_agent import AcpAgent, AcpError
from sidecar.store import Store, group_label

VERSION = "1.0.0"
MAX_UPLOAD = 8 * 1024 * 1024
CANNED = (
    "Here is a preview from the local stream.\n\n"
    "```python\nprint(1 + 1)\n```\n\n"
    "The sum is $2$.\n"
)
_EFFORT_FOR_MODE = {"fast": "low", "auto": "medium", "expert": "high", "heavy": "xhigh"}


class Cancels:
    def __init__(self) -> None:
        self._events: dict[str, set[asyncio.Event]] = {}

    def start(self, key: str) -> asyncio.Event:
        event = asyncio.Event()
        self._events.setdefault(key, set()).add(event)
        return event

    def stop(self, key: str) -> bool:
        events = self._events.get(key)
        if not events:
            return False
        for event in events:
            event.set()
        return True

    def clear(self, key: str, event: asyncio.Event) -> None:
        events = self._events.get(key)
        if events:
            events.discard(event)
            if not events:
                self._events.pop(key, None)


def data_dir() -> Path:
    raw = os.environ.get("GROKDECK_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".local" / "share" / "grokdeck"


def open_default_store() -> Store:
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root / "deck.db")
    if os.environ.get("GROKDECK_TESTING") != "1":
        store.import_legacy(root / "grokdeck.db")
    return store


def create_app(store: Store | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.acp_agent.close()

    app = FastAPI(title="GrokDeck", version=VERSION, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5175", "http://localhost:5175"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def reject_cross_origin_writes(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            allowed = {f"{request.url.scheme}://{request.url.netloc}",
                       "http://127.0.0.1:5175", "http://localhost:5175"}
            if origin and origin not in allowed:
                return Response(status_code=403)
        return await call_next(request)
    if store is None:
        store = open_default_store()
    app.state.store = store
    app.state.cancels = Cancels()
    app.state.models: dict[str, Any] = {"at": 0.0, "modes": xai.FALLBACK_MODES, "live": False}
    app.state.build_agents: dict[str, Any] = {}
    app.state.acp_agent: AcpAgent = AcpAgent(lambda: _settings(store))

    root = Path(__file__).resolve().parent.parent
    dist = root / "frontend" / "dist"
    if not dist.is_dir():
        dist = root / "dist"
    if dist.is_dir():
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "app": "grokdeck",
            "version": VERSION,
            "fakeStream": os.environ.get("GROKDECK_FAKE_STREAM") == "1",
            "key": secrets.configured(),
            "cli": cli_session.grok_bin() is not None,
            "signedIn": cli_session.signed_in(),
            "grokBot": shutil.which("grok-bot") is not None,
        }

    @app.get("/api/account")
    def account_get() -> dict[str, Any]:
        return cli_session.account_status()

    @app.post("/api/account/login")
    async def account_login() -> dict[str, Any]:
        return await cli_session.begin_login()

    @app.post("/api/account/logout")
    async def account_logout() -> dict[str, Any]:
        return await cli_session.sign_out()

    @app.post("/api/account/cancel")
    async def account_cancel() -> dict[str, Any]:
        return await cli_session.cancel_login()

    @app.get("/api/usage")
    def usage_get() -> dict[str, Any]:
        return usage.load_usage()

    @app.get("/api/key")
    def key_status() -> dict[str, bool]:
        return {"configured": secrets.configured()}

    @app.put("/api/key")
    def key_put(body: dict[str, Any]) -> dict[str, bool]:
        try:
            secrets.set_key(str(body.get("key") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        app.state.models["at"] = 0.0
        return {"configured": True}

    @app.delete("/api/key")
    def key_delete() -> dict[str, bool]:
        secrets.clear_key()
        app.state.models["at"] = 0.0
        return {"configured": False}

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        cached = app.state.models
        if time.time() - cached["at"] < 600 and cached["at"]:
            return {"modes": cached["modes"], "live": cached["live"]}
        key = secrets.get_key()
        if not key or os.environ.get("GROKDECK_FAKE_STREAM") == "1":
            cached.update(at=time.time(), modes=xai.FALLBACK_MODES, live=False)
            return {"modes": xai.FALLBACK_MODES, "live": False}
        try:
            ids = await xai.list_model_ids(key)
            modes = xai.modes_from_catalog(ids) if ids else xai.FALLBACK_MODES
            live = bool(ids)
        except (RuntimeError, OSError):
            modes = xai.FALLBACK_MODES
            live = False
        cached.update(at=time.time(), modes=modes, live=live)
        return {"modes": modes, "live": live}

    @app.get("/api/settings")
    def settings_get() -> dict[str, Any]:
        return _settings(store)

    @app.put("/api/settings")
    def settings_put(body: dict[str, Any]) -> dict[str, Any]:
        if "providers" in body:
            raise HTTPException(400, "Use the provider settings endpoint to change providers.")
        if body.get("subagentPolicy", "requested") not in {"requested", "auto", "off"}:
            raise HTTPException(400, "Subagent delegation is invalid.")
        if body.get("subagentQuality", "balanced") not in {"balanced", "strong"}:
            raise HTTPException(400, "Subagent quality is invalid.")
        if "subagentWorkers" in body and (type(body["subagentWorkers"]) is not int or body["subagentWorkers"] not in {1, 2, 3}):
            raise HTTPException(400, "Choose one to three subagents.")
        if "activeModel" in body:
            try:
                providers.resolve(store, str(body["activeModel"] or ""))
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        store.set_settings(body)
        return _settings(store)

    @app.get("/api/providers")
    def providers_get() -> dict[str, Any]:
        return {"providers": providers.list_providers(store), "models": providers.provider_models(store),
                "activeModel": _settings(store)["activeModel"]}

    @app.put("/api/providers")
    def providers_put(body: dict[str, Any]) -> dict[str, Any]:
        try:
            validated = providers.validate(body)
            key = body.get("key")
            if key is not None:
                if validated["protocol"] == "codex":
                    raise HTTPException(400, "Codex uses the official Codex CLI sign-in, not an API key here.")
                secrets.set_provider_key(validated["id"], str(key))
            providers.upsert(store, validated)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"providers": providers.list_providers(store), "models": providers.provider_models(store),
                "activeModel": _settings(store)["activeModel"]}

    @app.delete("/api/providers/{provider_id}")
    def providers_delete(provider_id: str) -> dict[str, Any]:
        if not providers.remove(store, provider_id):
            raise HTTPException(404, "Provider is not configured.")
        settings = _settings(store)
        if str(settings["activeModel"]).startswith(provider_id + ":"):
            store.set_settings({"activeModel": "grok:subscription"})
        return {"providers": providers.list_providers(store), "models": providers.provider_models(store),
                "activeModel": _settings(store)["activeModel"]}

    @app.put("/api/providers/{provider_id}/key")
    def provider_key_put(provider_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if provider_id in {"grok", "codex"}:
            raise HTTPException(400, "This provider does not use an API key here.")
        if not any(row["id"] == provider_id and row.get("configured") for row in providers.list_providers(store)):
            raise HTTPException(404, "Configure the provider before storing its key.")
        try:
            secrets.set_provider_key(provider_id, str(body.get("key") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"configured": True}

    @app.delete("/api/providers/{provider_id}/key")
    def provider_key_delete(provider_id: str) -> dict[str, Any]:
        if provider_id in {"grok", "codex"}:
            raise HTTPException(400, "This provider does not use an API key here.")
        try:
            secrets.clear_provider_key(provider_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if str(_settings(store)["activeModel"]).startswith(provider_id + ":"):
            store.set_settings({"activeModel": "grok:subscription"})
        return {"configured": False}

    @app.post("/api/agents/delegate")
    async def agents_delegate(body: dict[str, Any]) -> dict[str, Any]:
        task = str(body.get("task") or "").strip()
        if not task or len(task) > 20_000:
            raise HTTPException(400, "Worker task must be between one and 20,000 characters.")
        settings = _settings(store)
        if settings["subagentPolicy"] == "off":
            raise HTTPException(403, "Subagents are disabled in Settings → Providers.")
        requested = body.get("models") or ["auto"]
        if not isinstance(requested, list):
            raise HTTPException(400, "Worker models must be a list.")
        quality = str(body.get("quality") or settings["subagentQuality"])
        effort = str(body.get("effort") or ("high" if quality == "strong" else "medium"))
        if effort not in {"low", "medium", "high", "xhigh"}:
            raise HTTPException(400, "Worker reasoning effort is invalid.")
        try:
            selected = providers.worker_models(store, requested, quality, int(settings["subagentWorkers"]))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        results = await asyncio.gather(*(providers.run_worker(store, reference, task, effort) for reference in selected))
        return {"results": results}

    @app.get("/api/projects")
    def projects_list() -> list[dict[str, Any]]:
        return store.list_projects()

    @app.post("/api/projects")
    def projects_create(body: dict[str, Any]) -> dict[str, Any]:
        return store.create_project(str(body.get("name") or "Project"), str(body.get("instructions") or ""))

    @app.patch("/api/projects/{project_id}")
    def projects_patch(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        row = store.update_project(project_id, **body)
        if row is None:
            raise HTTPException(404, "project not found")
        return row

    @app.delete("/api/projects/{project_id}")
    def projects_delete(project_id: str) -> dict[str, bool]:
        store.delete_project(project_id)
        return {"ok": True}

    @app.get("/api/conversations")
    def conversations_list() -> list[dict[str, Any]]:
        return store.list_conversations()

    @app.post("/api/conversations")
    def conversations_create(body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        row = store.create_conversation(
            title=str(body.get("title") or "New chat"),
            mode=str(body.get("mode") or "fast"),
            model=_settings(store)["activeModel"],
            project_id=body.get("projectId"),
        )
        row["group"] = group_label(row["updated_at"])
        return row

    @app.get("/api/conversations/{cid}")
    def conversations_get(cid: str) -> dict[str, Any]:
        conv = store.get_conversation(cid)
        if conv is None:
            raise HTTPException(404, "conversation not found")
        messages = store.thread(cid)
        for msg in messages:
            msg["attachments"] = [
                {"id": a["id"], "name": a["name"], "mime": a["mime"]}
                for a in store.attachments_for(msg["id"])
            ]
        return {"conversation": conv, "messages": messages}

    @app.patch("/api/conversations/{cid}")
    def conversations_patch(cid: str, body: dict[str, Any]) -> dict[str, Any]:
        fields = {}
        if "title" in body:
            fields["title"] = str(body["title"])[:200]
        if "pinned" in body:
            fields["pinned"] = bool(body["pinned"])
        if "mode" in body:
            fields["mode"] = str(body["mode"])
        if "model" in body:
            try:
                providers.resolve(store, str(body["model"] or ""))
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            fields["model"] = str(body["model"])
        if "projectId" in body:
            fields["project_id"] = body["projectId"]
        row = store.update_conversation(cid, **fields)
        if row is None:
            raise HTTPException(404, "conversation not found")
        return row

    @app.delete("/api/conversations/{cid}")
    def conversations_delete(cid: str) -> dict[str, bool]:
        store.delete_conversation(cid)
        return {"ok": True}

    @app.post("/api/conversations/delete-all")
    def conversations_delete_all() -> dict[str, int]:
        return {"deleted": store.delete_all_conversations()}

    @app.post("/api/conversations/{cid}/branch")
    def branch(cid: str, body: dict[str, Any]) -> dict[str, Any]:
        if store.get_conversation(cid) is None:
            raise HTTPException(404, "conversation not found")
        message_id = str(body.get("messageId") or "")
        message = store.get_message(message_id)
        if message is None or message["conversation_id"] != cid:
            raise HTTPException(404, "message not found")
        updated = store.activate_sibling(message_id)
        if updated is None:
            raise HTTPException(404, "message not found")
        return conversations_get(cid)

    @app.post("/api/conversations/{cid}/feedback")
    def feedback(cid: str, body: dict[str, Any]) -> dict[str, Any]:
        msg = store.get_message(str(body.get("messageId") or ""))
        if msg is None or msg["conversation_id"] != cid:
            raise HTTPException(404, "message not found")
        value = body.get("value")
        if value not in ("up", "down", None, ""):
            raise HTTPException(400, "value must be up or down")
        updated = store.update_message(msg["id"], feedback=value or None)
        return updated or {}

    @app.get("/api/search")
    def search(q: str = "") -> list[dict[str, Any]]:
        hits = store.search(q)
        titled = []
        for hit in hits:
            conv = store.get_conversation(hit["conversation_id"])
            if conv is None:
                continue
            titled.append({**hit, "title": conv["title"]})
        return titled

    @app.get("/api/export")
    def export(fmt: str = "json") -> Any:
        if fmt == "md":
            return PlainTextResponse(store.export_markdown(), media_type="text/markdown")
        return store.export_payload()

    @app.post("/api/import")
    async def import_data(request: Request) -> dict[str, int]:
        ctype = request.headers.get("content-type", "")
        data = await request.body()
        if len(data) > 50 * 1024 * 1024:
            raise HTTPException(413, "import is larger than 50 MB")
        if "application/json" in ctype:
            try:
                return {"conversations": store.import_payload(json.loads(data))}
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc)) from exc
        text = data.decode("utf-8", "replace")
        title = "Imported"
        messages = [{"role": "user", "content": text}]
        return {"conversations": store.import_payload({"conversations": [{"title": title, "messages": messages}]})}

    @app.post("/api/attachments")
    async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "file is larger than 8 MB")
        root = data_dir() / "attachments"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        name = Path(file.filename or "file").name
        mime = file.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        row = store.add_attachment(name, mime, "")
        dest = root / f"{row['id']}-{name}"
        created = False
        try:
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created = True
            with os.fdopen(fd, "wb") as saved:
                saved.write(data)
        except OSError:
            if created:
                dest.unlink(missing_ok=True)
            store.delete_attachment(row["id"])
            raise
        store.set_attachment_path(row["id"], str(dest))
        row["path"] = str(dest)
        return {"id": row["id"], "name": name, "mime": mime}

    @app.get("/api/library")
    def library_index() -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        rows = store.list_attachments()
        for row in rows:
            mime = row["mime"] or ""
            kind = "Media" if mime.startswith("image/") else "Files"
            items.append({
                "id": row["id"],
                "name": row["name"],
                "size": Path(row["path"]).stat().st_size if row["path"] and Path(row["path"]).is_file() else 0,
                "mtime": int((row["created_at"] or 0) * 1000),
                "kind": kind,
                "url": f"/api/attachments/{row['id']}",
            })
        return items

    @app.get("/api/attachments/{attachment_id}")
    def attachment_file(attachment_id: str) -> FileResponse:
        row = store.get_attachment(attachment_id)
        if row is None:
            raise HTTPException(404, "file not found")
        path = Path(row["path"])
        root = (data_dir() / "attachments").resolve()
        resolved = path.resolve()
        if root not in resolved.parents or not resolved.is_file():
            raise HTTPException(404, "file not found")
        return FileResponse(resolved)

    @app.post("/api/conversations/{cid}/turns")
    async def turn(cid: str, body: dict[str, Any], request: Request) -> StreamingResponse:
        if store.get_conversation(cid) is None:
            raise HTTPException(404, "conversation not found")
        return StreamingResponse(_turn(store, app, cid, body, request, edit_of=None), media_type="text/event-stream")

    @app.post("/api/conversations/{cid}/regenerate")
    async def regenerate(cid: str, body: dict[str, Any], request: Request) -> StreamingResponse:
        msg = store.get_message(str(body.get("messageId") or ""))
        if msg is None or msg["conversation_id"] != cid or msg["role"] != "assistant":
            raise HTTPException(404, "assistant message not found")
        parent = msg["parent_id"]
        return StreamingResponse(
            _turn(store, app, cid, body, request, edit_of=None, parent_id=parent, skip_user=True),
            media_type="text/event-stream",
        )

    @app.post("/api/conversations/{cid}/edit")
    async def edit(cid: str, body: dict[str, Any], request: Request) -> StreamingResponse:
        msg = store.get_message(str(body.get("messageId") or ""))
        if msg is None or msg["conversation_id"] != cid or msg["role"] != "user":
            raise HTTPException(404, "user message not found")
        body = {**body, "text": body.get("text") or ""}
        return StreamingResponse(
            _turn(store, app, cid, body, request, edit_of=msg),
            media_type="text/event-stream",
        )

    @app.post("/api/conversations/{cid}/stop")
    def stop(cid: str) -> dict[str, bool]:
        return {"ok": app.state.cancels.stop(cid)}

    @app.post("/api/build/preview")
    def build_preview() -> dict[str, Any]:
        return _build_fixture(store)

    @app.post("/api/build/turns")
    async def build_turn(body: dict[str, Any]) -> StreamingResponse:
        settings = _settings(store)
        if not settings.get("buildCli"):
            raise HTTPException(409, "Build CLI is off. Enable it in Settings to drive the local grok CLI.")
        return StreamingResponse(_build_stream(app, store, body), media_type="text/event-stream")

    @app.post("/api/build/permission")
    def build_permission(body: dict[str, Any]) -> dict[str, bool]:
        fut = app.state.build_waiters.pop(str(body.get("id")), None) if hasattr(app.state, "build_waiters") else None
        if fut is None or fut.done():
            return {"ok": False}
        fut.set_result(bool(body.get("allow")))
        return {"ok": True}

    # ---- ACP-backed build sessions ---------------------------------------

    @app.get("/api/build/sessions")
    async def build_sessions_list() -> dict[str, Any]:
        """List ACP sessions. Never 500s; if the agent cannot start, returns []. """
        agent: AcpAgent = app.state.acp_agent
        try:
            sessions = await agent.list_sessions()
        except AcpError:
            sessions = []
        return {"sessions": sessions, "signedIn": cli_session.signed_in()}

    @app.post("/api/build/sessions")
    async def build_sessions_create(body: dict[str, Any]) -> dict[str, Any]:
        """Open a new ACP session. 400 if cwd is not an existing directory."""
        if not _settings(store)["buildCli"]:
            raise HTTPException(409, "Build CLI is off. Enable it in Settings to drive the local grok CLI.")
        cwd = str(body.get("cwd") or "").strip()
        if not cwd or not Path(cwd).expanduser().is_dir():
            raise HTTPException(400, "cwd is not an existing directory")
        instructions = str(body.get("instructions") or "")
        mode = str(body.get("mode") or "ask")
        agent: AcpAgent = app.state.acp_agent
        try:
            return await agent.open_session(cwd, instructions=instructions, mode=mode)
        except AcpError as exc:
            raise HTTPException(400, exc.message) from exc

    @app.post("/api/build/sessions/{session_id}/load")
    async def build_sessions_load(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Reattach to an ACP session and replay its transcript."""
        if not _settings(store)["buildCli"]:
            raise HTTPException(409, "Build CLI is off. Enable it in Settings to drive the local grok CLI.")
        cwd = str(body.get("cwd") or "").strip()
        if not cwd or not Path(cwd).expanduser().is_dir():
            raise HTTPException(400, "cwd is not an existing directory")
        agent: AcpAgent = app.state.acp_agent
        return await agent.load_session(session_id, cwd)

    @app.post("/api/build/sessions/{session_id}/prompt")
    async def build_sessions_prompt(session_id: str, body: dict[str, Any]) -> StreamingResponse:
        """Stream one ACP turn. Events: token, reasoning, tool, permission,
        title, command, done, error. The prompt text is never logged.
        """
        if not _settings(store)["buildCli"]:
            raise HTTPException(409, "Build CLI is off. Enable it in Settings to drive the local grok CLI.")
        text = str(body.get("text") or "").strip()
        cwd = str(body.get("cwd") or "").strip()
        if not text:
            raise HTTPException(400, "prompt required")
        if cwd and not Path(cwd).expanduser().is_dir():
            raise HTTPException(400, "working directory does not exist")
        model = str(body.get("model") or "").strip()
        effort = str(body.get("effort") or "").strip()
        mode = str(body.get("mode") or "").strip()
        worker_context = str(body.get("workerContext") or "")[:34_000]
        return StreamingResponse(
            _build_sessions_prompt_stream(app, session_id, text, model, effort, mode, worker_context),
            media_type="text/event-stream",
        )

    @app.post("/api/build/sessions/{session_id}/stop")
    async def build_sessions_stop(session_id: str) -> dict[str, bool]:
        agent: AcpAgent = app.state.acp_agent
        await agent.cancel(session_id)
        return {"ok": True}

    @app.post("/api/build/sessions/{session_id}/permission")
    async def build_sessions_permission(session_id: str, body: dict[str, Any]) -> dict[str, bool]:
        agent: AcpAgent = app.state.acp_agent
        await agent.answer_permission(body.get("id"), bool(body.get("allow")))
        return {"ok": True}

    @app.get("/api/build/files")
    def build_files(cwd: str = "") -> dict[str, Any]:
        """Local ``git status`` + truncated diff. No network."""
        path = Path(cwd or ".").expanduser()
        if not path.is_dir():
            return {"files": [], "diff": ""}
        files: list[dict[str, str]] = []
        diff_text = ""
        try:
            status = subprocess.run(
                ["git", "-C", str(path), "status", "--porcelain"],
                capture_output=True, text=True, timeout=10,
            )
            for line in (status.stdout or "").splitlines():
                if not line.strip():
                    continue
                code = line[:2].strip() or "?"
                fname = line[3:].strip()
                if fname:
                    files.append({"path": fname, "status": code})
        except (OSError, subprocess.TimeoutExpired):
            files = []
        try:
            diff = subprocess.run(
                ["git", "-C", str(path), "diff", "--no-color"],
                capture_output=True, text=True, timeout=10,
            )
            raw = (diff.stdout or "")
        except (OSError, subprocess.TimeoutExpired):
            raw = ""
        diff_text = "\n".join(raw.splitlines()[:200])
        branch = ""
        try:
            head = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            branch = (head.stdout or "").strip()
        except (OSError, subprocess.TimeoutExpired):
            branch = ""
        return {"files": files, "diff": diff_text, "branch": branch, "cwd": str(path)}

    @app.get("/api/build/catalog")
    def build_catalog() -> dict[str, Any]:
        """Installed grok plugin and skill names. Directory names only."""
        root = cli_session.auth_file().parent

        def names(folder: str) -> list[str]:
            path = root / folder
            if not path.is_dir():
                return []
            found = []
            try:
                children = list(path.iterdir())
            except OSError:
                return []
            for child in children:
                if child.name.startswith("."):
                    continue
                found.append(child.name)
            return sorted(found)[:80]

        return {"plugins": names("installed-plugins"), "skills": names("skills")}

    @app.post("/api/run")
    async def run_snippet(body: dict[str, Any]) -> dict[str, Any]:
        """Run one Python block locally. The process does not see the API key."""
        lang = str(body.get("lang") or "")
        code = str(body.get("code") or "")
        if lang not in {"python", "py"}:
            raise HTTPException(400, "Only a Python block can run here.")
        if not code.strip() or len(code) > 20_000:
            raise HTTPException(400, "Nothing to run.")
        tmp = tempfile.mkdtemp(prefix="grokdeck-run-")
        env = {"PATH": "/usr/bin:/bin", "HOME": tmp, "LANG": "C.UTF-8", "TMPDIR": tmp}
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", code,
                cwd=tmp,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), 2)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return {"ok": False, "output": "Timed out after 2 seconds."}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        text = (out or b"").decode("utf-8", "replace")[:4000]
        return {"ok": proc.returncode == 0, "output": text}

    @app.post("/api/voice/transcribe")
    async def voice_transcribe(file: UploadFile = File(...)) -> dict[str, str]:
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "Recording is too long.")
        if len(data) < 200:
            raise HTTPException(400, "Recording was empty.")
        try:
            text = await voice.transcribe(data, file.content_type or "audio/webm")
        except voice.VoiceError as exc:
            raise HTTPException(exc.status, exc.message) from exc
        return {"text": text}

    @app.post("/api/voice/speak")
    async def voice_speak(body: dict[str, Any]) -> Response:
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "Nothing to speak.")
        try:
            audio, ctype = await voice.synthesize(text)
        except voice.VoiceError as exc:
            raise HTTPException(exc.status, exc.message) from exc
        return Response(content=audio, media_type=ctype)

    @app.post("/api/imagine")
    async def imagine(body: dict[str, Any]) -> dict[str, Any]:
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "prompt required")
        if os.environ.get("GROKDECK_FAKE_STREAM") == "1":
            return {"image": None, "note": "Image generation is not called in the local preview."}
        key = secrets.get_key()
        if not key:
            raise HTTPException(401, "Images need an xAI API key in Settings.")
        payload = {"model": "grok-2-image", "prompt": prompt, "n": 1}
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{xai.API}/images/generations",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, "image request was rejected")
        data = resp.json()
        url = ((data.get("data") or [{}])[0] or {}).get("url")
        return {"image": url, "note": None}

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> Any:
        if full_path.startswith("api/"):
            raise HTTPException(404, "not found")
        if not dist.is_dir():
            return HTMLResponse("GrokDeck UI is not built. From the repo, run: make dev", status_code=503)
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")

    return app


def _settings(store: Store) -> dict[str, Any]:
    defaults = {
        "theme": "light",
        "mode": "fast",
        "customInstructions": "",
        "buildCli": False,
        "autoApprove": False,
        "allowFs": True,
        "allowTerminal": False,
        "defaultCwd": str(Path.home()),
        "search": False,
        "activeModel": "grok:subscription",
        "subagentPolicy": "requested",
        "subagentQuality": "balanced",
        "subagentWorkers": 2,
    }
    defaults.update(store.get_settings())
    return defaults


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _turn(store: Store, app: FastAPI, cid: str, body: dict[str, Any], request: Request, *,
                edit_of: dict[str, Any] | None, parent_id: str | None = None, skip_user: bool = False):
    conv = store.get_conversation(cid)
    assert conv is not None
    mode = str(body.get("mode") or conv["mode"] or "fast")
    store.update_conversation(cid, mode=mode)
    settings = _settings(store)
    text = str(body.get("text") or "")
    attachment_ids = [str(i) for i in (body.get("attachmentIds") or [])]

    if edit_of is not None:
        parent_id = edit_of["parent_id"]
        user = store.add_message(cid, "user", text, parent_id=parent_id)
    elif skip_user:
        user = store.get_message(parent_id) if parent_id else None
    else:
        leaf = conv["active_leaf"]
        user = store.add_message(cid, "user", text, parent_id=leaf)
    if user is None:
        yield _sse("error", {"message": "nothing to regenerate"})
        return
    if attachment_ids and not skip_user:
        store.link_attachments(user["id"], attachment_ids)
    assistant = store.add_message(cid, "assistant", "", parent_id=user["id"], status="streaming")
    yield _sse("ready", {"userId": user["id"], "assistantId": assistant["id"], "conversationId": cid})

    cancel = app.state.cancels.start(cid)
    parts: list[str] = []
    thoughts: list[str] = []
    cites: list[str] = []
    status = "done"
    try:
        worker_context = str(body.get("workerContext") or "")[:34_000]
        async for event in _produce(app, store, cid, user, mode, settings, cancel, worker_context):
            if await request.is_disconnected():
                cancel.set()
            if event["type"] == "token":
                parts.append(event["text"])
                yield _sse("token", {"text": event["text"]})
            elif event["type"] == "reasoning":
                thoughts.append(event["text"])
                yield _sse("reasoning", {"text": event["text"]})
            elif event["type"] == "citation":
                if event["url"] not in cites:
                    cites.append(event["url"])
                    yield _sse("citation", {"url": event["url"]})
            elif event["type"] == "error":
                status = "error"
                parts.append(event["message"])
                yield _sse("error", {"message": event["message"]})
                break
            elif event["type"] == "done":
                if event.get("stop") == "stopped":
                    status = "stopped"
                break
    finally:
        app.state.cancels.clear(cid, cancel)
        store.update_message(
            assistant["id"],
            content="".join(parts),
            reasoning="".join(thoughts),
            status=status,
            citations=cites,
        )
        yield _sse("done", {"status": status, "assistantId": assistant["id"]})


async def _produce(app: FastAPI, store: Store, cid: str, user: dict[str, Any], mode: str,
                   settings: dict[str, Any], cancel: asyncio.Event, worker_context: str = ""):
    if os.environ.get("GROKDECK_FAKE_STREAM") == "1":
        delay = float(os.environ.get("GROKDECK_FAKE_DELAY", "0"))
        if mode in ("expert", "heavy"):
            yield {"type": "reasoning", "text": "Checking the arithmetic."}
        for index in range(0, len(CANNED), 12):
            if cancel.is_set():
                yield {"type": "done", "stop": "stopped"}
                return
            yield {"type": "token", "text": CANNED[index:index + 12]}
            await asyncio.sleep(delay)
        yield {"type": "citation", "url": "https://example.com/local-preview"}
        yield {"type": "done", "stop": "stop"}
        return

    conversation = store.get_conversation(cid) or {}
    try:
        provider, model = providers.resolve(store, str(conversation.get("model") or settings["activeModel"]))
    except ValueError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    messages = _api_messages(store, cid, settings.get("customInstructions") or "", worker_context)
    if provider["protocol"] != "grok":
        async for event in providers.stream_chat(provider, model, messages, _EFFORT_FOR_MODE.get(mode), cancel.is_set):
            yield event
        return

    key = secrets.get_key()
    if not key:
        if cli_session.signed_in():
            async for event in cli_session.stream_reply(messages, mode, cancel):
                yield event
            return
        yield {"type": "error", "message": "Sign in from the account menu. Nothing was sent."}
        return

    modes = (await _modes(app))["modes"]
    chosen = next((m for m in modes if m["id"] == mode), modes[0])
    async for event in xai.stream_chat(
        key,
        chosen["model"],
        messages,
        effort=chosen.get("effort"),
        search=bool(settings.get("search")),
        cancel_check=cancel.is_set,
    ):
        yield event


async def _modes(app: FastAPI) -> dict[str, Any]:
    cached = app.state.models
    if cached["at"] and time.time() - cached["at"] < 600:
        return cached
    key = secrets.get_key()
    if not key:
        return {"modes": xai.FALLBACK_MODES, "live": False}
    try:
        ids = await xai.list_model_ids(key)
        modes = xai.modes_from_catalog(ids) if ids else xai.FALLBACK_MODES
        live = bool(ids)
    except (RuntimeError, OSError):
        modes = xai.FALLBACK_MODES
        live = False
    cached.update(at=time.time(), modes=modes, live=live)
    return cached


def _api_messages(store: Store, cid: str, instructions: str, worker_context: str = "") -> list[dict[str, Any]]:
    system = "You are GrokDeck, a local chat app. Be direct."
    project_bits = []
    conv = store.get_conversation(cid)
    if conv and conv.get("project_id"):
        for project in store.list_projects():
            if project["id"] == conv["project_id"] and project["instructions"]:
                project_bits.append(project["instructions"])
    if instructions:
        project_bits.append(instructions)
    if worker_context:
        project_bits.append("Specialist reports from explicitly requested subagents. Verify their claims before relying on them:\n" + worker_context)
    if project_bits:
        system += "\n\n" + "\n\n".join(project_bits)
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for msg in store.thread(cid):
        if msg["role"] not in ("user", "assistant") or msg["status"] == "streaming":
            continue
        if msg["role"] == "assistant" and not msg["content"]:
            continue
        content: Any = msg["content"]
        if msg["role"] == "user":
            content = _with_attachments(store, msg)
        out.append({"role": msg["role"], "content": content})
    return out


def _with_attachments(store: Store, msg: dict[str, Any]) -> Any:
    files = store.attachments_for(msg["id"])
    if not files:
        return msg["content"]
    parts: list[dict[str, Any]] = [{"type": "text", "text": msg["content"]}]
    for item in files:
        path = Path(item["path"])
        if not path.is_file():
            continue
        mime = item["mime"] or ""
        if mime.startswith("image/"):
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        else:
            text = path.read_text(encoding="utf-8", errors="replace")[:20_000]
            parts.append({"type": "text", "text": f"\nAttached file {item['name']}:\n{text}"})
    return parts


def _build_fixture(store: Store) -> dict[str, Any]:
    settings = _settings(store)
    return {
        "live": False,
        "note": "Preview layout. The local grok CLI is off, so this is not an agent turn.",
        "cwd": settings["defaultCwd"],
        "steps": [
            {"id": "1", "label": "Read the workspace", "state": "done"},
            {"id": "2", "label": "Edit a file", "state": "done"},
            {"id": "3", "label": "Run the check", "state": "pending"},
        ],
        "files": [
            {"path": "README.md", "status": "M"},
            {"path": "src/main.ts", "status": "A"},
        ],
        "diff": "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n",
        "terminal": "$ make test\npreview only — no command was run\n",
    }


async def _build_stream(app: FastAPI, store: Store, body: dict[str, Any]):
    """Drive one grok CLI turn. Off unless Settings → build CLI is on."""
    from sidecar.grok_bridge import BridgeError, GrokAgent

    settings = _settings(store)
    cwd = Path(str(body.get("cwd") or settings["defaultCwd"])).expanduser()
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        yield _sse("error", {"message": "prompt required"})
        return
    if not cwd.is_dir():
        yield _sse("error", {"message": "working directory does not exist"})
        return

    loop = asyncio.get_running_loop()
    app.state.build_waiters = getattr(app.state, "build_waiters", {})

    async def needs_approval(info: dict[str, Any]) -> bool:
        if info.get("op") == "read":
            return bool(settings.get("allowFs", True))
        if settings.get("autoApprove"):
            return True
        if info.get("kind") == "terminal" and not settings.get("allowTerminal"):
            return False
        fut: asyncio.Future[bool] = loop.create_future()
        token = f"perm-{time.time_ns()}"
        app.state.build_waiters[token] = fut
        yield_note = {"type": "permission", "id": token, "detail": info.get("detail") or info.get("path") or ""}
        await agent.emit(yield_note)
        try:
            return await asyncio.wait_for(fut, timeout=120)
        except asyncio.TimeoutError:
            return False
        finally:
            app.state.build_waiters.pop(token, None)

    async def fs_read(path: Path) -> str | None:
        safe = _inside(cwd, path)
        if safe is None or not safe.is_file():
            return None
        return safe.read_text(encoding="utf-8", errors="replace")

    async def fs_write(path: Path, content: str) -> bool:
        safe = _inside(cwd, path)
        if safe is None:
            return False
        safe.parent.mkdir(parents=True, exist_ok=True)
        safe.write_text(content, encoding="utf-8")
        return True

    terminals: dict[str, dict[str, Any]] = {}

    async def terminal(req: dict[str, Any]) -> dict[str, Any]:
        op = req.get("op")
        if op == "create":
            proc = await asyncio.create_subprocess_exec(
                req.get("command") or "true",
                *[str(a) for a in (req.get("args") or [])],
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            tid = str(proc.pid)
            terminals[tid] = {"proc": proc, "chunks": []}
            return {"terminalId": tid}
        tid = str(req.get("terminalId") or "")
        slot = terminals.get(tid)
        if slot is None:
            return {"output": "", "exitCode": None}
        proc = slot["proc"]
        if op == "output":
            return {"output": "".join(slot["chunks"]), "truncated": False}
        if op == "wait":
            data = await proc.stdout.read() if proc.stdout else b""
            slot["chunks"].append(data.decode("utf-8", "replace"))
            code = await proc.wait()
            return {"output": "".join(slot["chunks"]), "exitCode": code}
        if op == "kill":
            with suppress(ProcessLookupError):
                proc.kill()
            return {"ok": True}
        return {}

    agent = GrokAgent(
        cwd,
        session_id=f"deck-{time.time_ns()}",
        needs_approval=needs_approval,
        fs_read=fs_read,
        fs_write=fs_write,
        terminal=terminal if settings.get("allowTerminal") else None,
        allow_fs=bool(settings.get("allowFs", True)),
        log=lambda _event, _data: None,
    )
    queue = agent.subscribe()
    try:
        await agent.start()
        yield _sse("ready", {"cwd": str(cwd)})
        runner = asyncio.create_task(agent.prompt(prompt))
        while not runner.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            yield _sse(event.get("type") or "update", _public_event(event))
        result = await runner
        while not queue.empty():
            event = queue.get_nowait()
            yield _sse(event.get("type") or "update", _public_event(event))
        yield _sse("done", {"stop": (result or {}).get("stopReason") or "end"})
    except BridgeError as exc:
        yield _sse("error", {"message": exc.message})
    finally:
        agent.unsubscribe(queue)
        await agent.close()


async def _build_sessions_prompt_stream(
    app: FastAPI, session_id: str, text: str, model: str = "", effort: str = "", mode: str = "", worker_context: str = "",
):
    """SSE stream of one ACP turn. Yields token/reasoning/tool/permission/title/
    command/done/error events. The prompt text is never reflected in the
    response or logged.
    """
    agent: AcpAgent = app.state.acp_agent
    try:
        agen = agent.prompt(session_id, text, model, effort, mode, worker_context)
    except AcpError as exc:
        yield _sse("error", {"message": exc.message})
        return
    try:
        async for event in agen:
            kind = event.get("type")
            if not isinstance(kind, str):
                continue
            if kind not in {"token", "reasoning", "tool", "permission",
                            "title", "command", "done", "error"}:
                continue
            # Text deltas are lossless; diagnostic strings disclose their cap.
            data: dict[str, Any] = {}
            deltas: dict[str, str] = {}
            marker = "… [truncated]"
            for k, v in event.items():
                if k == "type":
                    continue
                if kind in {"token", "reasoning"} and k in {"text", "verbose", "delta"} and isinstance(v, str):
                    deltas[k] = v
                    continue
                if isinstance(v, str) and len(v) > 8000:
                    v = v[:8000 - len(marker)] + marker
                data[k] = v
            # Python slices preserve Unicode code points, including astral text.
            chunks = max((max(1, (len(v) + 7999) // 8000) for v in deltas.values()), default=1)
            for index in range(chunks):
                yield _sse(kind, {**data, **{k: v[index * 8000:(index + 1) * 8000] for k, v in deltas.items()}})
    except AcpError as exc:
        yield _sse("error", {"message": exc.message})


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    """Drop anything that is not a small status field. Text deltas are the reply."""
    keep = {}
    for key in ("type", "delta", "text", "title", "stopReason", "path", "op", "detail", "id", "entries"):
        if key in event:
            value = event[key]
            if isinstance(value, str) and len(value) > 8000:
                value = value[:8000]
            keep[key] = value
    return keep


def _inside(cwd: Path, path: Path) -> Path | None:
    candidate = path if path.is_absolute() else cwd / path
    try:
        resolved = candidate.resolve()
        resolved.relative_to(cwd.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _module_app() -> FastAPI:
    if os.environ.get("GROKDECK_TESTING") == "1" and not os.environ.get("GROKDECK_HOME"):
        return create_app(Store(":memory:"))
    return create_app()


app = _module_app()
