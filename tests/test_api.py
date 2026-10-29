"""API tests against the fake stream. No xAI call and no real grok CLI."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sidecar.grokdeck_server import Cancels, create_app  # noqa: E402
from sidecar.store import Store  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cli_session():
    from sidecar import cli_session

    cli_session._session.update(proc=None, state="idle", message="")
    yield
    proc = cli_session._session.get("proc")
    if proc is not None and getattr(proc, "returncode", None) is None:
        proc.kill()
    cli_session._session.update(proc=None, state="idle", message="")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROKDECK_TESTING", "1")
    monkeypatch.setenv("GROKDECK_FAKE_STREAM", "1")
    monkeypatch.setenv("GROKDECK_SECRET_PATH", str(tmp_path / "key"))
    monkeypatch.setenv("GROKDECK_FAKE_DELAY", "0")
    monkeypatch.setenv("GROKDECK_HOME", str(tmp_path / "deck-home"))
    monkeypatch.setenv("GROKDECK_GROK_HOME", str(tmp_path / "grok-home"))
    monkeypatch.setenv("GROKDECK_GROK_BIN", str(tmp_path / "missing-grok"))
    store = Store(tmp_path / "deck.db")
    app = create_app(store)
    with TestClient(app) as test:
        yield test
    store.close()


def _read_stream(client: TestClient, path: str, body: dict) -> str:
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200
        return "".join(response.iter_text())


def test_health_and_key_roundtrip_hides_the_secret(client: TestClient):
    secret = "sk-test-value-not-for-logs"
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["app"] == "grokdeck"
    assert secret not in health.text
    put = client.put("/api/key", json={"key": secret})
    assert put.json()["configured"] is True
    assert secret not in put.text
    assert secret not in client.get("/api/key").text
    assert secret not in client.get("/api/health").text
    client.delete("/api/key")
    assert client.get("/api/key").json()["configured"] is False


def test_three_worker_setting_persists_and_rejects_more(client: TestClient):
    saved = client.put("/api/settings", json={"subagentWorkers": 3})
    assert saved.status_code == 200
    assert client.get("/api/settings").json()["subagentWorkers"] == 3
    rejected = client.put("/api/settings", json={"subagentWorkers": 4})
    assert rejected.status_code == 400
    assert client.get("/api/settings").json()["subagentWorkers"] == 3


def test_turn_streams_and_clears_streaming(client: TestClient):
    conv = client.post("/api/conversations", json={"mode": "fast"}).json()
    raw = _read_stream(client, f"/api/conversations/{conv['id']}/turns", {"text": "hello", "mode": "fast"})
    assert "event: token" in raw
    assert "event: done" in raw
    thread = client.get(f"/api/conversations/{conv['id']}").json()
    statuses = [m["status"] for m in thread["messages"]]
    assert "streaming" not in statuses
    assert any(m["role"] == "assistant" and "print(1 + 1)" in m["content"] for m in thread["messages"])


def test_parallel_chat_sessions_keep_complete_threads(client: TestClient):
    ids = [client.post("/api/conversations", json={}).json()["id"] for _ in range(4)]

    def run(cid: str) -> None:
        assert client.patch(f"/api/conversations/{cid}", json={"mode": "expert"}).status_code == 200
        for index in range(10):
            raw = _read_stream(client, f"/api/conversations/{cid}/turns", {"text": f"turn {index}"})
            assert '"status": "done"' in raw
            assert "event: error" not in raw

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, ids))
    for cid in ids:
        messages = client.get(f"/api/conversations/{cid}").json()["messages"]
        assert len(messages) == 20
        assert all(message["status"] != "streaming" for message in messages)


def test_conversation_model_patch_validates_provider(client: TestClient):
    from sidecar import providers

    store = client.app.state.store
    providers.upsert(store, {"id": "mistral", "models": ["mistral-large-4"]})
    conversation = client.post("/api/conversations", json={}).json()
    path = f"/api/conversations/{conversation['id']}"
    changed = client.patch(path, json={"model": "mistral:mistral-large-4"})
    assert changed.status_code == 200
    assert changed.json()["model"] == "mistral:mistral-large-4"
    rejected = client.patch(path, json={"model": "mistral:unknown"})
    assert rejected.status_code == 400
    assert client.get(path).json()["conversation"]["model"] == "mistral:mistral-large-4"


def test_provider_key_failure_does_not_save_provider(client: TestClient):
    rejected = client.put("/api/providers", json={
        "id": "mistral", "models": ["mistral-large-4"], "key": "bad\nkey",
    })
    assert rejected.status_code == 400
    mistral = next(row for row in client.get("/api/providers").json()["providers"] if row["id"] == "mistral")
    assert mistral["configured"] is False


def test_keyring_failure_does_not_save_provider(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from sidecar import secrets

    def unavailable(*_args):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(secrets, "set_provider_key", unavailable)
    response = client.put("/api/providers", json={
        "id": "mistral", "models": ["mistral-large-4"], "key": "fixture-key",
    })
    assert response.status_code == 503
    mistral = next(row for row in client.get("/api/providers").json()["providers"] if row["id"] == "mistral")
    assert mistral["configured"] is False


def test_edit_branches_and_regenerate(client: TestClient):
    conv = client.post("/api/conversations", json={}).json()
    _read_stream(client, f"/api/conversations/{conv['id']}/turns", {"text": "first draft"})
    thread = client.get(f"/api/conversations/{conv['id']}").json()
    user = next(m for m in thread["messages"] if m["role"] == "user")
    assistant = next(m for m in thread["messages"] if m["role"] == "assistant")
    _read_stream(
        client,
        f"/api/conversations/{conv['id']}/edit",
        {"messageId": user["id"], "text": "revised draft"},
    )
    edited = client.get(f"/api/conversations/{conv['id']}").json()
    assert edited["messages"][0]["content"] == "revised draft"
    assert edited["messages"][0]["sibling_count"] == 2
    client.post(f"/api/conversations/{conv['id']}/branch", json={"messageId": user["id"]})
    back = client.get(f"/api/conversations/{conv['id']}").json()
    assert back["messages"][0]["content"] == "first draft"
    _read_stream(
        client,
        f"/api/conversations/{conv['id']}/regenerate",
        {"messageId": assistant["id"], "mode": "expert"},
    )
    again = client.get(f"/api/conversations/{conv['id']}").json()
    assert again["messages"][-1]["role"] == "assistant"
    assert again["messages"][-1]["reasoning"]


def test_branch_rejects_message_from_another_conversation(client: TestClient):
    store = client.app.state.store
    first = store.create_conversation()
    second = store.create_conversation()
    message = store.add_message(second["id"], "user", "second chat")
    before = store.get_conversation(second["id"])["active_leaf"]

    response = client.post(f"/api/conversations/{first['id']}/branch", json={"messageId": message["id"]})

    assert response.status_code == 404
    assert store.get_conversation(second["id"])["active_leaf"] == before


def test_import_rejects_invalid_json_without_changing_chats(client: TestClient):
    invalid = client.post("/api/import", content=b'{"conversations": [}', headers={"Content-Type": "application/json"})
    assert invalid.status_code == 400
    malformed = client.post("/api/import", json={"conversations": "bad"})
    assert malformed.status_code == 400
    assert client.get("/api/conversations").json() == []


def test_cross_origin_form_cannot_modify_local_data(client: TestClient):
    blocked = client.post("/api/import", content=b"injected chat", headers={
        "Content-Type": "text/plain", "Origin": "https://unrelated.example",
    })
    assert blocked.status_code == 403
    assert client.get("/api/conversations").json() == []
    allowed = client.post("/api/import", content=b"my chat", headers={
        "Content-Type": "text/plain", "Origin": "http://testserver",
    })
    assert allowed.status_code == 200


def test_project_and_json_export_import_routes_preserve_links(client: TestClient):
    project = client.post("/api/projects", json={"name": "Workspace", "instructions": "first"}).json()
    updated = client.patch(f"/api/projects/{project['id']}", json={"instructions": "second"})
    assert updated.status_code == 200
    assert updated.json()["instructions"] == "second"
    conversation = client.post("/api/conversations", json={"title": "Keep", "projectId": project["id"]}).json()
    payload = client.get("/api/export").json()
    assert payload["version"] == 3
    assert payload["conversations"][0]["project_id"] == project["id"]
    client.post("/api/conversations/delete-all")
    client.delete(f"/api/projects/{project['id']}")

    imported = client.post("/api/import", json=payload)
    assert imported.status_code == 200
    assert imported.json() == {"conversations": 1}
    restored_project = client.get("/api/projects").json()[0]
    restored_chat = client.get("/api/conversations").json()[0]
    assert restored_project["instructions"] == "second"
    assert restored_chat["title"] == conversation["title"]
    assert restored_chat["project_id"] == restored_project["id"]


def test_build_catalog_lists_local_names_only(client: TestClient):
    root = Path(os.environ["GROKDECK_GROK_HOME"])
    (root / "skills" / "review").mkdir(parents=True)
    (root / "installed-plugins" / "tools").mkdir(parents=True)
    (root / "skills" / ".hidden").mkdir()
    response = client.get("/api/build/catalog")
    assert response.status_code == 200
    assert response.json() == {"plugins": ["tools"], "skills": ["review"]}


def test_search_settings_project_and_missing_key_message(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    conv = client.post("/api/conversations", json={}).json()
    _read_stream(client, f"/api/conversations/{conv['id']}/turns", {"text": "zebra-bridge"})
    hits = client.get("/api/search", params={"q": "zebra-bridge"}).json()
    assert hits and hits[0]["title"] == "zebra-bridge"
    saved = client.put("/api/settings", json={"theme": "dark", "buildCli": False, "allowTerminal": True}).json()
    assert saved["theme"] == "dark"
    assert saved["allowTerminal"] is True
    project = client.post("/api/projects", json={"name": "Deck", "instructions": "Be brief"}).json()
    assert project["name"] == "Deck"
    monkeypatch.setenv("GROKDECK_FAKE_STREAM", "0")
    raw = _read_stream(client, f"/api/conversations/{conv['id']}/turns", {"text": "live?"})
    assert "Sign in from the account menu" in raw
    assert "Nothing was sent." in raw
    monkeypatch.setenv("GROKDECK_FAKE_STREAM", "1")


def test_run_python_snippet_hides_the_environment(client: TestClient):
    done = client.post("/api/run", json={"lang": "python", "code": "print(6 * 7)"})
    assert done.status_code == 200
    assert done.json()["output"].strip() == "42"
    denied = client.post("/api/run", json={"lang": "bash", "code": "echo hi"})
    assert denied.status_code == 400


def test_delete_all_conversations_clears_the_list(client: TestClient):
    first = client.post("/api/conversations", json={}).json()
    client.post("/api/conversations", json={})
    done = client.post("/api/conversations/delete-all")
    assert done.status_code == 200
    assert done.json()["deleted"] == 2
    assert client.get("/api/conversations").json() == []
    assert client.get(f"/api/conversations/{first['id']}").status_code == 404


def test_usage_route_uses_the_loader(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("sidecar.usage.load_usage", lambda: {
        "ok": True,
        "signedIn": True,
        "tier": "Grok Pro",
        "period": "weekly",
        "usedPercent": 88.0,
        "resetsAt": "2026-10-15T05:25:15+00:00",
        "products": [{"id": "GrokBuild", "label": "Grok Build", "percent": 88.0, "color": "#1084fe"}],
        "onDemandCap": 0,
        "onDemandUsed": 0,
        "prepaid": 0,
        "cliDataRetentionOptOut": True,
        "error": None,
    })
    body = client.get("/api/usage")
    assert body.status_code == 200
    assert body.json()["usedPercent"] == 88.0
    assert "email" not in body.json()


def test_build_preview_does_not_claim_a_live_turn(client: TestClient):
    preview = client.post("/api/build/preview").json()
    assert preview["live"] is False
    denied = client.post("/api/build/turns", json={"prompt": "do work"})
    assert denied.status_code == 409


def test_stop_interrupts_the_fake_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import asyncio

    from sidecar.grokdeck_server import _produce

    monkeypatch.setenv("GROKDECK_FAKE_STREAM", "1")
    monkeypatch.setenv("GROKDECK_FAKE_DELAY", "0.01")
    monkeypatch.setenv("GROKDECK_TESTING", "1")
    store = Store(tmp_path / "deck.db")
    app = create_app(store)
    conv = store.create_conversation()
    user = store.add_message(conv["id"], "user", "hi")
    cancel = app.state.cancels.start(conv["id"])

    async def run() -> list[str]:
        kinds: list[str] = []
        agen = _produce(app, store, conv["id"], user, "fast", {}, cancel)
        async for event in agen:
            kinds.append(event["type"])
            if event["type"] == "token":
                cancel.set()
        return kinds

    kinds = asyncio.run(run())
    assert "token" in kinds
    assert kinds[-1] == "done"
    assert len([k for k in kinds if k == "token"]) < 20
    store.close()


def test_stop_interrupts_a_silent_cli_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import asyncio

    from sidecar import cli_session

    binary = tmp_path / "silent-grok"
    binary.write_text("#!/usr/bin/env python3\nimport sys\nimport time\nif sys.argv[1:] == ['agent', '--help']:\n    print('stdio')\nelse:\n    time.sleep(30)\n")
    binary.chmod(0o755)
    monkeypatch.setenv("GROKDECK_GROK_BIN", str(binary))

    async def run():
        cancel = asyncio.Event()

        async def consume():
            return [event async for event in cli_session.stream_reply([{"role": "user", "content": "hi"}], "fast", cancel)]

        turn = asyncio.create_task(consume())
        await asyncio.sleep(0.1)
        cancel.set()
        return await asyncio.wait_for(turn, 2)

    assert asyncio.run(run()) == [{"type": "done", "stop": "stopped"}]


def test_cli_streaming_json_text_and_errors_are_not_shown_as_protocol():
    from sidecar.cli_session import _parse_line

    assert _parse_line('{"type":"text","data":"Hello"}') == {"type": "token", "text": "Hello"}
    assert _parse_line('{"type":"thought","data":"Checking"}') == {"type": "reasoning", "text": "Checking"}
    assert _parse_line('{"type":"error","message":"private detail"}') == {
        "type": "error", "message": "Grok CLI reported an error. Check your sign-in and usage.",
    }
    assert _parse_line('{"type":"usage","usage":{"input_tokens":1}}') is None


def test_cli_stream_drops_json_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from sidecar import cli_session

    binary = tmp_path / "typed-grok"
    binary.write_text("#!/usr/bin/env python3\nimport sys\n"
                      "if sys.argv[1:] == ['agent', '--help']:\n print('stdio')\n"
                      "else:\n print('{\"type\":\"usage\",\"usage\":{\"input_tokens\":1}}')\n"
                      " print('{\"type\":\"text\",\"data\":\"plain answer\"}')\n")
    binary.chmod(0o755)
    monkeypatch.setenv("GROKDECK_GROK_BIN", str(binary))

    async def run():
        return [event async for event in cli_session.stream_reply([{"role": "user", "content": "hi"}],
                                                                   "fast", asyncio.Event())]

    assert asyncio.run(run()) == [
        {"type": "token", "text": "plain answer"}, {"type": "done", "stop": "stop"},
    ]


def test_subscription_chat_does_not_silently_drop_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from sidecar import cli_session

    _install_fake(tmp_path, monkeypatch, _FAKE_GROK, "fake-grok")

    async def run():
        return [event async for event in cli_session.stream_reply([
            {"role": "user", "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ]},
        ], "fast", asyncio.Event())]

    assert asyncio.run(run()) == [{
        "type": "error", "message": "Grok CLI chat cannot send images. Use Grok web chat or an xAI API key.",
    }]


def test_stop_reaches_overlapping_turns():
    cancels = Cancels()
    first = cancels.start("chat")
    second = cancels.start("chat")
    assert cancels.stop("chat") is True
    assert first.is_set() and second.is_set()
    cancels.clear("chat", first)
    assert cancels.stop("chat") is True
    assert second.is_set()
    assert cancels.stop("other") is False


_FAKE_GROK = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if args == ["agent", "--help"]:
    print("stdio")
    raise SystemExit(0)
home = Path(os.environ["GROKDECK_GROK_HOME"])
home.mkdir(parents=True, exist_ok=True)
auth = home / "auth.json"
if args[:1] == ["login"]:
    auth.write_text("placeholder-credential")
    print("Opening your browser to sign in")
    raise SystemExit(0)
if args[:1] == ["logout"]:
    print("bearer should-not-leak")
    auth.unlink(missing_ok=True)
    raise SystemExit(0)
print('{"sessionUpdate":"agent_message_chunk","content":{"text":"cli-ok"}}')
"""

_SLOW_GROK = """#!/usr/bin/env python3
import sys
import time

if sys.argv[1:] == ["agent", "--help"]:
    print("stdio")
    raise SystemExit(0)
if sys.argv[1:2] == ["login"]:
    time.sleep(30)
raise SystemExit(0)
"""


def _install_fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, name: str) -> None:
    binary = tmp_path / name
    binary.write_text(source)
    binary.chmod(0o755)
    monkeypatch.setenv("GROKDECK_GROK_BIN", str(binary))


def test_cli_prefers_official_agent_over_unrelated_grok_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from sidecar import cli_session

    official = tmp_path / ".grok/bin/grok"
    official.parent.mkdir(parents=True)
    official.write_text("#!/bin/sh\necho 'stdio  Run the agent over stdio'\n")
    official.chmod(0o755)
    other = tmp_path / "other-grok"
    other.write_text("#!/bin/sh\necho 'Usage: unrelated grok'\n")
    other.chmod(0o755)
    monkeypatch.delenv("GROKDECK_GROK_BIN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli_session.shutil, "which", lambda name: str(other))

    assert cli_session.grok_bin() == str(official)
    official.unlink()
    assert cli_session.grok_bin() is None


def test_cli_override_must_support_agent_stdio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from sidecar import cli_session

    binary = tmp_path / "unrelated-grok"
    binary.write_text("#!/bin/sh\necho 'Usage: unrelated grok'\n")
    binary.chmod(0o755)
    monkeypatch.setenv("GROKDECK_GROK_BIN", str(binary))

    assert cli_session.grok_bin() is None
    assert cli_session.account_status()["cli"] is False


def test_login_without_a_cli_stays_signed_out(client: TestClient):
    status = client.post("/api/account/login").json()
    assert status["cli"] is False
    assert status["signedIn"] is False
    assert status["state"] == "failed"
    assert "auth.json" not in client.get("/api/account").text


def test_browser_login_and_logout_hide_the_credential(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _install_fake(tmp_path, monkeypatch, _FAKE_GROK, "fake-grok")
    idle = client.get("/api/account").json()
    assert idle["signedIn"] is False
    assert idle["cli"] is True
    started = client.post("/api/account/login")
    body = started.json()
    assert body["signedIn"] is True
    assert body["state"] == "signed-in"
    hidden = "placeholder-credential"
    assert hidden not in started.text
    assert hidden not in client.get("/api/account").text
    assert hidden not in client.get("/api/health").text
    out = client.post("/api/account/logout")
    assert out.json()["signedIn"] is False
    assert out.json()["state"] == "idle"
    assert hidden not in out.text
    assert "should-not-leak" not in out.text
    assert client.get("/api/account").json()["signedIn"] is False


def test_cancel_stops_a_waiting_login(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _install_fake(tmp_path, monkeypatch, _SLOW_GROK, "slow-grok")
    started = client.post("/api/account/login").json()
    assert started["state"] == "waiting"
    assert started["signedIn"] is False
    stopped = client.post("/api/account/cancel").json()
    assert stopped["state"] == "idle"
    assert stopped["signedIn"] is False


def test_signed_in_chat_uses_the_fake_cli(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _install_fake(tmp_path, monkeypatch, _FAKE_GROK, "fake-grok")
    monkeypatch.setenv("GROKDECK_FAKE_STREAM", "0")
    home = Path(os.environ["GROKDECK_GROK_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text("placeholder-credential")
    conv = client.post("/api/conversations", json={"mode": "fast"}).json()
    raw = _read_stream(client, f"/api/conversations/{conv['id']}/turns", {"text": "hello from the test", "mode": "fast"})
    assert "cli-ok" in raw
    assert "placeholder-credential" not in raw
    assert "hello from the test" not in raw
    thread = client.get(f"/api/conversations/{conv['id']}").json()
    assistant = next(m for m in thread["messages"] if m["role"] == "assistant")
    assert assistant["content"] == "cli-ok"
    leftover = []
    for path in Path(tempfile.gettempdir()).glob("grokdeck-prompt-*"):
        try:
            if "hello from the test" in path.read_text(encoding="utf-8", errors="replace"):
                leftover.append(path.name)
        except OSError:
            continue
    assert leftover == []


def test_library_upload_lists_the_file_and_imagine_stays_local(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    uploaded = client.post("/api/attachments", files={"file": ("note.txt", b"hello library", "text/plain")})
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["name"] == "note.txt"
    assert "hello library" not in uploaded.text
    stored = client.app.state.store.get_attachment(body["id"])
    assert Path(stored["path"]).stat().st_mode & 0o777 == 0o600
    assert Path(stored["path"]).parent.stat().st_mode & 0o777 == 0o700
    library = client.get("/api/library").json()
    match = next(item for item in library if item["id"] == body["id"])
    assert match["kind"] == "Files"
    assert match["name"] == "note.txt"
    fetched = client.get(f"/api/attachments/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.content == b"hello library"
    preview = client.post("/api/imagine", json={"prompt": "a cup"})
    assert preview.status_code == 200
    assert preview.json()["image"] is None
    monkeypatch.setenv("GROKDECK_FAKE_STREAM", "0")
    denied = client.post("/api/imagine", json={"prompt": "a cup"})
    assert denied.status_code == 401
    assert denied.json()["detail"] == "Images need an xAI API key in Settings."


def test_voice_transcribes_and_speaks_without_leaking_the_key(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from sidecar.voice import VoiceError
    from sidecar.voice import transcribe as real_transcribe

    secret = "sk-voice-test-value"
    missing = client.post("/api/voice/transcribe", files={"file": ("speech.webm", b"x" * 300, "audio/webm")})
    assert missing.status_code == 401
    assert secret not in missing.text
    assert "Bearer" not in missing.text

    async def fake_transcribe(data: bytes, mime: str) -> str:
        assert secret.encode() not in data
        assert mime
        return "heard you"

    async def fake_speak(text: str) -> tuple[bytes, str]:
        assert secret not in text
        return b"ID3-not-a-key", "audio/mpeg"

    monkeypatch.setattr("sidecar.voice.transcribe", fake_transcribe)
    heard = client.post("/api/voice/transcribe", files={"file": ("speech.webm", b"x" * 300, "audio/webm")})
    assert heard.status_code == 200
    assert heard.json()["text"] == "heard you"
    assert secret not in heard.text

    short = client.post("/api/voice/transcribe", files={"file": ("speech.webm", b"x", "audio/webm")})
    assert short.status_code == 400

    monkeypatch.setattr("sidecar.voice.synthesize", fake_speak)
    spoken = client.post("/api/voice/speak", json={"text": "Hello there"})
    assert spoken.status_code == 200
    assert spoken.headers["content-type"].startswith("audio/mpeg")
    assert spoken.content == b"ID3-not-a-key"
    assert client.post("/api/voice/speak", json={"text": "  "}).status_code == 400

    import asyncio

    class FakeResponse:
        status_code = 401
        content = secret.encode() + b" rejected"
        headers: dict[str, str] = {}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            del args
            assert kwargs["headers"]["Authorization"] == f"Bearer {secret}"
            return FakeResponse()

    monkeypatch.setattr("sidecar.voice.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("sidecar.voice.secrets.get_key", lambda: secret)

    async def run():
        try:
            await real_transcribe(b"audio-bytes", "audio/webm")
        except VoiceError as exc:
            return exc
        raise AssertionError("expected VoiceError")

    caught = asyncio.run(run())
    assert caught.status == 401
    assert secret not in caught.message
