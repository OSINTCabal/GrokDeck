"""Offline store tests. No network and no grok CLI."""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sidecar.store import Store, group_label  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    db = Store(tmp_path / "deck.db")
    yield db
    db.close()


def test_group_labels_do_not_overlap():
    noon = datetime(2026, 10, 14, 12, 0, tzinfo=datetime.now().astimezone().tzinfo)
    now = noon.timestamp()
    stamps = {
        "Today": noon.timestamp(),
        "Yesterday": datetime(2026, 10, 13, 23, 0, tzinfo=noon.tzinfo).timestamp(),
        "Previous 7 days": datetime(2026, 10, 11, 12, 0, tzinfo=noon.tzinfo).timestamp(),
        "Older": datetime(2026, 10, 1, 12, 0, tzinfo=noon.tzinfo).timestamp(),
    }
    labels = {name: group_label(stamp, now) for name, stamp in stamps.items()}
    assert labels == {name: name for name in stamps}
    assert len(set(labels.values())) == 4


def test_create_and_title_from_first_message(store: Store):
    conv = store.create_conversation()
    assert conv["title"] == "New chat"
    store.add_message(conv["id"], "user", "Ship the rebuild\nmore")
    assert store.get_conversation(conv["id"])["title"] == "Ship the rebuild"


def test_pinned_sorts_first(store: Store):
    older = store.create_conversation(title="Older")
    newer = store.create_conversation(title="Newer")
    store.update_conversation(older["id"], pinned=True, updated_at=time.time() - 100)
    store.update_conversation(newer["id"], updated_at=time.time())
    rows = store.list_conversations()
    assert rows[0]["id"] == older["id"]
    assert rows[0]["group"] in {"Today", "Yesterday", "Previous 7 days", "Older"}


def test_branch_edit_keeps_the_other_path(store: Store):
    conv = store.create_conversation()
    user = store.add_message(conv["id"], "user", "first")
    store.add_message(conv["id"], "assistant", "answer one", parent_id=user["id"])
    edited = store.add_message(conv["id"], "user", "second", parent_id=None)
    store.add_message(conv["id"], "assistant", "answer two", parent_id=edited["id"])
    thread = store.thread(conv["id"])
    assert [m["content"] for m in thread] == ["second", "answer two"]
    store.activate_sibling(user["id"])
    again = store.thread(conv["id"])
    assert [m["content"] for m in again] == ["first", "answer one"]
    assert again[0]["sibling_count"] == 2
    assert len(again[0]["sibling_ids"]) == 2


def test_search_finds_message_text(store: Store):
    conv = store.create_conversation()
    store.add_message(conv["id"], "user", "unique-needle about bridges")
    hits = store.search("unique-needle")
    assert hits and hits[0]["conversation_id"] == conv["id"]


def test_settings_roundtrip_includes_toggles(store: Store):
    saved = store.set_settings({"buildCli": True, "allowTerminal": True, "theme": "dark", "ignored": 1})
    assert saved["buildCli"] is True
    assert saved["allowTerminal"] is True
    assert saved["theme"] == "dark"
    assert "ignored" not in saved
    general = store.set_settings({"theme": "ocean"})
    assert general["theme"] == "ocean"
    rejected = store.set_settings({"theme": "nope"})
    assert rejected["theme"] == "ocean"


def test_export_import_roundtrip(store: Store):
    project = store.create_project("Keep project", "Use short replies")
    conv = store.create_conversation(title="Keep", model="mistral:mistral-large-4", project_id=project["id"])
    store.update_conversation(conv["id"], pinned=True)
    user = store.add_message(conv["id"], "user", "hello")
    old = store.add_message(conv["id"], "assistant", "old answer", parent_id=user["id"])
    store.update_message(old["id"], feedback="up", citations=["https://example.com/source"])
    store.add_message(conv["id"], "assistant", "new answer", parent_id=user["id"])
    store.activate_sibling(old["id"])
    payload = store.export_payload()
    other = Store(":memory:")
    assert other.import_payload(payload) == 1
    imported = other.list_conversations()[0]
    titles = [c["title"] for c in other.list_conversations()]
    assert "Keep" in titles
    assert "hello" in other.export_markdown()
    assert imported["model"] == "mistral:mistral-large-4"
    assert imported["pinned"] == 1
    assert imported["project_id"] == other.list_projects()[0]["id"]
    assert other.list_projects()[0]["instructions"] == "Use short replies"
    thread = other.thread(imported["id"])
    assert [item["content"] for item in thread] == ["hello", "old answer"]
    assert thread[-1]["sibling_count"] == 2
    assert thread[-1]["feedback"] == "up"
    assert thread[-1]["citations"] == ["https://example.com/source"]
    assert other.search("old answer")
    assert {item["content"] for item in other.export_payload()["conversations"][0]["messages"]} == {
        "hello", "old answer", "new answer",
    }
    other.close()


def test_malformed_import_does_not_write_partial_data(store: Store):
    import pytest

    payload = {"projects": [{"name": "would be imported"}], "conversations": [
        {"title": "valid", "messages": [{"role": "user", "content": "hello"}]},
        {"title": "invalid", "messages": "not a list"},
    ]}
    with pytest.raises(ValueError):
        store.import_payload(payload)
    assert store.list_projects() == []
    assert store.list_conversations() == []
    with pytest.raises(ValueError):
        store.import_payload({"conversations": [{"title": "bad time", "created_at": "NaN"}]})
    with pytest.raises(ValueError):
        store.import_payload({"conversations": [{"title": "bad time", "created_at": 1e300}]})
    assert store.list_conversations() == []


def test_db_file_is_private(tmp_path: Path):
    path = tmp_path / "deck.db"
    Store(path).close()
    assert path.stat().st_mode & 0o777 == 0o600
