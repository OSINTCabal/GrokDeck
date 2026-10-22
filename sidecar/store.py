"""Local SQLite for conversations, branches, projects, and search.

Message text is stored here on purpose. The file mode is 0600. The xAI key
is not stored in this database.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT 'New chat',
  pinned INTEGER NOT NULL DEFAULT 0,
  model TEXT,
  mode TEXT NOT NULL DEFAULT 'fast',
  project_id TEXT,
  active_leaf TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  reasoning TEXT NOT NULL DEFAULT '',
  parent_id TEXT,
  created_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'done',
  feedback TEXT,
  citations TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS attachments (
  id TEXT PRIMARY KEY,
  message_id TEXT,
  name TEXT NOT NULL,
  mime TEXT,
  path TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  instructions TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content, reasoning, message_id UNINDEXED, conversation_id UNINDEXED, tokenize='porter'
);
"""


def _id() -> str:
    return uuid.uuid4().hex


def _import_time(value: Any, fallback: float) -> float:
    if value is None:
        return fallback
    try:
        timestamp = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid timestamp in import data.") from exc
    if not math.isfinite(timestamp) or not 0 <= timestamp <= 253402300799:
        raise ValueError("Invalid timestamp in import data.")
    return timestamp


def group_label(updated_at: float, now: float | None = None) -> str:
    """Calendar buckets. A row is in exactly one of them."""
    moment = datetime.fromtimestamp(now if now is not None else time.time()).astimezone()
    then = datetime.fromtimestamp(updated_at).astimezone()
    delta = (moment.date() - then.date()).days
    if delta <= 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    if delta < 7:
        return "Previous 7 days"
    return "Older"


def _locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class Store:
    def __init__(self, path: str | Path):
        self._lock = RLock()
        self._memory = str(path) == ":memory:"
        self.path = None if self._memory else Path(path)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                self.path.chmod(0o600)
            else:
                os.close(fd)
        self.conn = sqlite3.connect(
            ":memory:" if self._memory else str(self.path),
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        if not self._memory:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.path.chmod(0o600)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.fts = False
        try:
            self.conn.executescript(FTS)
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False
        self.conn.commit()

    @_locked
    def close(self) -> None:
        self.conn.close()

    # ---- settings / projects --------------------------------------------
    @_locked
    def get_settings(self) -> dict[str, Any]:
        rows = self.conn.execute("SELECT key, value FROM settings").fetchall()
        out: dict[str, Any] = {}
        for row in rows:
            try:
                out[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                out[row["key"]] = row["value"]
        return out

    @_locked
    def set_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "theme", "mode", "customInstructions", "buildCli", "autoApprove",
            "allowFs", "allowTerminal", "defaultCwd", "search", "legacyImported",
            "providers", "activeModel", "subagentPolicy", "subagentQuality", "subagentWorkers",
        }
        themes = {"light", "dark", "system", "grokbot", "slate", "paper", "ocean", "forest"}
        for key, value in values.items():
            if key not in allowed:
                continue
            if key == "theme" and value not in themes:
                continue
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
        self.conn.commit()
        return self.get_settings()

    @_locked
    def create_project(self, name: str, instructions: str = "") -> dict[str, Any]:
        row = {
            "id": _id(),
            "name": name.strip() or "Project",
            "instructions": instructions,
            "created_at": time.time(),
        }
        self.conn.execute(
            "INSERT INTO projects(id, name, instructions, created_at) VALUES(?,?,?,?)",
            (row["id"], row["name"], row["instructions"], row["created_at"]),
        )
        self.conn.commit()
        return row

    @_locked
    def list_projects(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    @_locked
    def update_project(self, project_id: str, **fields: Any) -> dict[str, Any] | None:
        current = self.conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if current is None:
            return None
        name = fields.get("name", current["name"])
        instructions = fields.get("instructions", current["instructions"])
        self.conn.execute(
            "UPDATE projects SET name=?, instructions=? WHERE id=?",
            (name, instructions, project_id),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())

    @_locked
    def delete_project(self, project_id: str) -> None:
        self.conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        self.conn.execute("UPDATE conversations SET project_id=NULL WHERE project_id=?", (project_id,))
        self.conn.commit()

    # ---- conversations ---------------------------------------------------
    @_locked
    def create_conversation(self, *, title: str = "New chat", mode: str = "fast",
                            model: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        now = time.time()
        row = {
            "id": _id(),
            "title": title,
            "pinned": 0,
            "model": model,
            "mode": mode,
            "project_id": project_id,
            "active_leaf": None,
            "created_at": now,
            "updated_at": now,
            "deleted": 0,
        }
        self.conn.execute(
            """INSERT INTO conversations
               (id, title, pinned, model, mode, project_id, active_leaf, created_at, updated_at, deleted)
               VALUES (:id,:title,:pinned,:model,:mode,:project_id,:active_leaf,:created_at,:updated_at,:deleted)""",
            row,
        )
        self.conn.commit()
        return row

    @_locked
    def list_conversations(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM conversations WHERE deleted=0
               ORDER BY pinned DESC, updated_at DESC"""
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["group"] = group_label(item["updated_at"])
            out.append(item)
        return out

    @_locked
    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM conversations WHERE id=? AND deleted=0", (conversation_id,)
        ).fetchone()
        return dict(row) if row else None

    @_locked
    def update_conversation(self, conversation_id: str, **fields: Any) -> dict[str, Any] | None:
        current = self.get_conversation(conversation_id)
        if current is None:
            return None
        allowed = ("title", "pinned", "model", "mode", "project_id", "active_leaf", "updated_at")
        for key in allowed:
            if key in fields:
                current[key] = fields[key]
        if "updated_at" not in fields:
            current["updated_at"] = time.time()
        self.conn.execute(
            """UPDATE conversations SET title=?, pinned=?, model=?, mode=?, project_id=?,
               active_leaf=?, updated_at=? WHERE id=?""",
            (current["title"], int(bool(current["pinned"])), current["model"], current["mode"],
             current["project_id"], current["active_leaf"], current["updated_at"], conversation_id),
        )
        self.conn.commit()
        return self.get_conversation(conversation_id)

    @_locked
    def delete_conversation(self, conversation_id: str) -> None:
        self.conn.execute("UPDATE conversations SET deleted=1 WHERE id=?", (conversation_id,))
        self.conn.commit()

    @_locked
    def delete_all_conversations(self) -> int:
        cursor = self.conn.execute("UPDATE conversations SET deleted=1 WHERE deleted=0")
        self.conn.commit()
        return int(cursor.rowcount or 0)

    # ---- messages / branches ---------------------------------------------
    @_locked
    def add_message(self, conversation_id: str, role: str, content: str = "", *,
                    parent_id: str | None = None, reasoning: str = "",
                    status: str = "done", citations: list[str] | None = None) -> dict[str, Any]:
        row = {
            "id": _id(),
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "reasoning": reasoning,
            "parent_id": parent_id,
            "created_at": time.time(),
            "status": status,
            "feedback": None,
            "citations": json.dumps(citations or []),
        }
        self.conn.execute(
            """INSERT INTO messages
               (id, conversation_id, role, content, reasoning, parent_id, created_at, status, feedback, citations)
               VALUES (:id,:conversation_id,:role,:content,:reasoning,:parent_id,:created_at,:status,:feedback,:citations)""",
            row,
        )
        self._index(row["id"], conversation_id, content, reasoning)
        self.conn.execute(
            "UPDATE conversations SET active_leaf=?, updated_at=? WHERE id=?",
            (row["id"], row["created_at"], conversation_id),
        )
        if role == "user" and content.strip():
            conv = self.get_conversation(conversation_id)
            if conv and (conv["title"] in ("", "New chat")):
                title = content.strip().splitlines()[0][:80]
                self.conn.execute("UPDATE conversations SET title=? WHERE id=?", (title, conversation_id))
        self.conn.commit()
        return self.get_message(row["id"])  # type: ignore[return-value]

    @_locked
    def get_message(self, message_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return _message(row) if row else None

    @_locked
    def update_message(self, message_id: str, **fields: Any) -> dict[str, Any] | None:
        current = self.conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if current is None:
            return None
        data = dict(current)
        for key in ("content", "reasoning", "status", "feedback"):
            if key in fields:
                data[key] = fields[key]
        if "citations" in fields:
            data["citations"] = json.dumps(fields["citations"] or [])
        self.conn.execute(
            "UPDATE messages SET content=?, reasoning=?, status=?, feedback=?, citations=? WHERE id=?",
            (data["content"], data["reasoning"], data["status"], data["feedback"], data["citations"], message_id),
        )
        self._index(message_id, data["conversation_id"], data["content"], data["reasoning"])
        self.conn.commit()
        return self.get_message(message_id)

    @_locked
    def thread(self, conversation_id: str) -> list[dict[str, Any]]:
        conv = self.get_conversation(conversation_id)
        if conv is None:
            return []
        leaf = conv["active_leaf"]
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        while leaf and leaf not in seen:
            seen.add(leaf)
            msg = self.get_message(leaf)
            if msg is None or msg["conversation_id"] != conversation_id:
                break
            chain.append(msg)
            leaf = msg["parent_id"]
        chain.reverse()
        counts = self._sibling_counts(conversation_id)
        for msg in chain:
            info = counts.get(msg["id"])
            msg["sibling_index"] = info[0] if info else 0
            msg["sibling_count"] = info[1] if info else 1
            msg["sibling_ids"] = [item["id"] for item in self.siblings(msg["id"])]
        return chain

    @_locked
    def siblings(self, message_id: str) -> list[dict[str, Any]]:
        msg = self.get_message(message_id)
        if msg is None:
            return []
        rows = self.conn.execute(
            """SELECT * FROM messages
               WHERE conversation_id=? AND role=?
                 AND ((parent_id IS NULL AND ? IS NULL) OR parent_id=?)
               ORDER BY created_at ASC""",
            (msg["conversation_id"], msg["role"], msg["parent_id"], msg["parent_id"]),
        ).fetchall()
        return [_message(r) for r in rows]

    @_locked
    def activate_sibling(self, message_id: str) -> dict[str, Any] | None:
        """Point the conversation at this sibling, walking down its latest child."""
        msg = self.get_message(message_id)
        if msg is None:
            return None
        leaf = msg
        while True:
            child = self.conn.execute(
                """SELECT * FROM messages WHERE parent_id=? AND conversation_id=?
                   ORDER BY created_at DESC LIMIT 1""",
                (leaf["id"], msg["conversation_id"]),
            ).fetchone()
            if child is None:
                break
            leaf = _message(child)
        return self.update_conversation(msg["conversation_id"], active_leaf=leaf["id"])

    def _sibling_counts(self, conversation_id: str) -> dict[str, tuple[int, int]]:
        rows = self.conn.execute(
            "SELECT id, parent_id, role, created_at FROM messages WHERE conversation_id=? ORDER BY created_at",
            (conversation_id,),
        ).fetchall()
        groups: dict[tuple[str | None, str], list[str]] = {}
        for row in rows:
            groups.setdefault((row["parent_id"], row["role"]), []).append(row["id"])
        out: dict[str, tuple[int, int]] = {}
        for ids in groups.values():
            for index, mid in enumerate(ids):
                out[mid] = (index, len(ids))
        return out

    def _index(self, message_id: str, conversation_id: str, content: str, reasoning: str) -> None:
        if not self.fts:
            return
        self.conn.execute("DELETE FROM messages_fts WHERE message_id=?", (message_id,))
        self.conn.execute(
            "INSERT INTO messages_fts(content, reasoning, message_id, conversation_id) VALUES(?,?,?,?)",
            (content, reasoning, message_id, conversation_id),
        )

    @_locked
    def search(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        if self.fts:
            safe = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in query).strip()
            if safe:
                try:
                    rows = self.conn.execute(
                        """SELECT message_id, conversation_id,
                                  snippet(messages_fts, 0, '', '', '…', 16) AS snippet
                           FROM messages_fts WHERE messages_fts MATCH ? LIMIT ?""",
                        (f'"{safe}"', limit),
                    ).fetchall()
                    return [dict(r) for r in rows]
                except sqlite3.OperationalError:
                    pass
        like = f"%{query}%"
        rows = self.conn.execute(
            """SELECT id AS message_id, conversation_id, substr(content, 1, 160) AS snippet
               FROM messages WHERE content LIKE ? OR reasoning LIKE ? LIMIT ?""",
            (like, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- attachments -----------------------------------------------------
    @_locked
    def add_attachment(self, name: str, mime: str, path: str) -> dict[str, Any]:
        row = {"id": _id(), "message_id": None, "name": name, "mime": mime, "path": path, "created_at": time.time()}
        self.conn.execute(
            "INSERT INTO attachments(id, message_id, name, mime, path, created_at) VALUES(?,?,?,?,?,?)",
            (row["id"], None, name, mime, path, row["created_at"]),
        )
        self.conn.commit()
        return row

    @_locked
    def link_attachments(self, message_id: str, ids: list[str]) -> list[dict[str, Any]]:
        for aid in ids:
            self.conn.execute("UPDATE attachments SET message_id=? WHERE id=?", (message_id, aid))
        self.conn.commit()
        return self.attachments_for(message_id)

    @_locked
    def get_attachment(self, attachment_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        return dict(row) if row else None

    @_locked
    def set_attachment_path(self, attachment_id: str, path: str) -> None:
        self.conn.execute("UPDATE attachments SET path=? WHERE id=?", (path, attachment_id))
        self.conn.commit()

    @_locked
    def delete_attachment(self, attachment_id: str) -> None:
        self.conn.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
        self.conn.commit()

    @_locked
    def list_attachments(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, name, mime, path, created_at FROM attachments ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    @_locked
    def attachments_for(self, message_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM attachments WHERE message_id=?", (message_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---- import / export -------------------------------------------------
    @_locked
    def export_payload(self) -> dict[str, Any]:
        convos = []
        for conv in self.list_conversations():
            msgs = self.conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at",
                (conv["id"],),
            ).fetchall()
            convos.append({
                "id": conv["id"],
                "title": conv["title"],
                "mode": conv["mode"],
                "model": conv["model"],
                "project_id": conv["project_id"],
                "active_leaf": conv["active_leaf"],
                "created_at": conv["created_at"],
                "updated_at": conv["updated_at"],
                "pinned": conv["pinned"],
                "messages": [_message(m) for m in msgs],
            })
        return {"version": 3, "conversations": convos, "projects": self.list_projects()}

    @_locked
    def export_markdown(self) -> str:
        chunks: list[str] = []
        for conv in self.export_payload()["conversations"]:
            chunks.append(f"# {conv['title']}\n")
            for msg in conv["messages"]:
                who = "User" if msg["role"] == "user" else "Assistant"
                chunks.append(f"## {who}\n\n{msg['content']}\n")
        return "\n".join(chunks).strip() + "\n"

    @_locked
    def import_payload(self, payload: dict[str, Any]) -> int:
        if not isinstance(payload, dict):
            raise ValueError("Invalid import data.")
        projects = payload.get("projects") or []
        conversations = payload.get("conversations") or []
        if not isinstance(projects, list) or not isinstance(conversations, list):
            raise ValueError("Import projects and conversations must be lists.")
        for project in projects:
            if not isinstance(project, dict):
                raise ValueError("Invalid project in import data.")
        for conv in conversations:
            if not isinstance(conv, dict) or not isinstance(conv.get("messages", []), list):
                raise ValueError("Invalid conversation in import data.")
            messages = conv.get("messages") or []
            if any(not isinstance(msg, dict) for msg in messages):
                raise ValueError("Invalid message in import data.")
            ids = [msg.get("id") for msg in messages if msg.get("id") is not None]
            if any(not isinstance(mid, str) or not mid for mid in ids) or len(ids) != len(set(ids)):
                raise ValueError("Invalid message IDs in import data.")
            if any(msg.get("parent_id") not in (None, *ids) for msg in messages):
                raise ValueError("Invalid message parent in import data.")
            if conv.get("active_leaf") is not None and conv["active_leaf"] not in ids:
                raise ValueError("Invalid active branch in import data.")

        now = time.time()
        project_ids: dict[str, str] = {}
        try:
            self.conn.execute("BEGIN")
            for project in projects:
                new_id = _id()
                if project.get("id") is not None:
                    project_ids[str(project["id"])] = new_id
                self.conn.execute(
                    "INSERT INTO projects(id, name, instructions, created_at) VALUES(?,?,?,?)",
                    (new_id, str(project.get("name") or "Project"), str(project.get("instructions") or ""),
                     _import_time(project.get("created_at"), now)),
                )
            for conv in conversations:
                conv_id = _id()
                messages = conv.get("messages") or []
                message_ids = {msg["id"]: _id() for msg in messages if msg.get("id") is not None}
                generated_ids = [message_ids[msg["id"]] if msg.get("id") is not None else _id() for msg in messages]
                active = message_ids.get(conv.get("active_leaf")) if conv.get("active_leaf") else (generated_ids[-1] if generated_ids else None)
                project_id = project_ids.get(str(conv.get("project_id"))) if conv.get("project_id") else None
                self.conn.execute(
                    """INSERT INTO conversations
                       (id, title, pinned, model, mode, project_id, active_leaf, created_at, updated_at, deleted)
                       VALUES (?,?,?,?,?,?,?,?,?,0)""",
                    (conv_id, str(conv.get("title") or "Imported"), int(bool(conv.get("pinned"))),
                     str(conv["model"]) if conv.get("model") else None, str(conv.get("mode") or "fast"),
                    project_id, active, _import_time(conv.get("created_at"), now),
                     _import_time(conv.get("updated_at"), now)),
                )
                for index, msg in enumerate(messages):
                    msg_id = generated_ids[index]
                    parent = message_ids.get(msg.get("parent_id")) if "parent_id" in msg else (generated_ids[index - 1] if index else None)
                    content = str(msg.get("content") or "")
                    reasoning = str(msg.get("reasoning") or "")
                    self.conn.execute(
                        """INSERT INTO messages
                           (id, conversation_id, role, content, reasoning, parent_id, created_at, status, feedback, citations)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (msg_id, conv_id, str(msg.get("role") or "user"), content, reasoning, parent,
                         _import_time(msg.get("created_at"), now + index * 0.000001),
                         str(msg.get("status") or "done"),
                         msg.get("feedback") if msg.get("feedback") in ("up", "down") else None,
                         json.dumps(msg.get("citations") if isinstance(msg.get("citations"), list) else [])),
                    )
                    self._index(msg_id, conv_id, content, reasoning)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return len(conversations)

    @_locked
    def import_legacy(self, legacy_path: Path) -> int:
        """Copy the pre-rebuild database if it is present. Read-only on that file."""
        if self.get_settings().get("legacyImported"):
            return 0
        if not legacy_path.exists():
            return 0
        uri = f"file:{legacy_path}?mode=ro"
        try:
            src = sqlite3.connect(uri, uri=True)
        except sqlite3.Error:
            return 0
        src.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "sessions" not in tables or "messages" not in tables:
                self.set_settings({"legacyImported": True})
                return 0
            sessions = src.execute(
                "SELECT id, title, pinned, created_at, updated_at, model FROM sessions"
            ).fetchall()
            copied = 0
            for session in sessions:
                conv = self.create_conversation(title=session["title"] or "New chat")
                self.update_conversation(
                    conv["id"],
                    pinned=int(session["pinned"] or 0),
                    model=session["model"],
                    updated_at=session["updated_at"] or time.time(),
                )
                msgs = src.execute(
                    "SELECT role, text, thought, created_at FROM messages WHERE session_id=? ORDER BY turn, id",
                    (session["id"],),
                ).fetchall()
                for msg in msgs:
                    self.add_message(
                        conv["id"],
                        msg["role"] or "user",
                        msg["text"] or "",
                        reasoning=msg["thought"] or "",
                    )
                copied += 1
            self.set_settings({"legacyImported": True})
            return copied
        except sqlite3.Error:
            return 0
        finally:
            src.close()


def _message(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    raw = data.get("citations") or "[]"
    if isinstance(raw, str):
        try:
            data["citations"] = json.loads(raw)
        except json.JSONDecodeError:
            data["citations"] = []
    return data
