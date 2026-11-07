"""Build-session API tests. Spawn NOTHING real — drive a small python fake.

``GROKDECK_ACP_BIN`` is set to ``python <tmp/...>`` so the bridge spawns a
fake ACP server we control. The fake speaks just enough of JSON-RPC for the
list / load / prompt routes. Network is never touched.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sidecar.grokdeck_server import create_app  # noqa: E402
from sidecar.store import Store  # noqa: E402

# This fake implements just enough ACP for the build-sessions routes.
_FAKE_ACP_SRC = textwrap.dedent(
    '''\
    #!/usr/bin/env python3
    """Just enough ACP for the build-sessions routes. No network."""

    import json
    import sys


    SESSIONS = [
        {
            "sessionId": "sess-1",
            "cwd": "/tmp/grokdeck-scratch-1",
            "_meta": {"title": "First session"},
            "title": "First session",
            "updatedAt": 1700000000,
        },
        {
            "sessionId": "sess-2",
            "cwd": "/tmp/grokdeck-scratch-2",
            "_meta": {"title": "Second session"},
            "title": "Second session",
            "updatedAt": 1700000010,
        },
    ]
    CONFIG = set()


    LOAD_UPDATES = [
        {"update": {"sessionUpdate": "available_commands_update",
                    "availableCommands": [{"name": "review", "description": "Review code",
                                           "input": {"private": "not for the UI"}}]}},
        {"update": {"sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "Hello there"}}},
        {"update": {"sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": "thinking..."}}},
        {"update": {"sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "Hi, ready when you are."}}},
        {"update": {"sessionUpdate": "tool_call", "toolCallId": "tool-1",
                    "title": "echo",
                    "_meta": {"x.ai/tool": {"name": "echo", "label": "Echo",
                                            "read_only": True}},
                    "status": "in_progress"}},
    ]


    def respond(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()


    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        rid = msg.get("id")
        if method == "initialize":
            respond({"jsonrpc": "2.0", "id": rid,
                     "result": {"protocolVersion": 1,
                                "agentCapabilities": {}}})
        elif method == "authenticate":
            respond({"jsonrpc": "2.0", "id": rid,
                     "result": {"_meta": {"subscription_tier": "fake"}}})
        elif method == "session/list":
            respond({"jsonrpc": "2.0", "id": rid, "result": SESSIONS})
        elif method == "session/new":
            params = msg.get("params") or {}
            sid = "new-" + str(rid)
            respond({"jsonrpc": "2.0", "method": "session/update",
                     "params": {"sessionId": sid, "update": {
                         "sessionUpdate": "available_commands_update",
                         "availableCommands": [{"name": "review", "description": "Review code"}]}}})
            respond({"jsonrpc": "2.0", "id": rid,
                     "result": {"sessionId": sid,
                                "cwd": params.get("cwd", ""),
                                "models": {"availableModels": [
                                    {"modelId": "grok-4.7", "_meta": {"reasoningEfforts": ["high", "low"]}}
                                ]},
                                "configOptions": [
                                    {"id": "model", "currentValue": "grok-4.7"},
                                    {"id": "reasoning_effort", "currentValue": "low",
                                     "options": [{"value": "high"}, {"value": "low"}]},
                                ]}})
        elif method == "session/load":
            for u in LOAD_UPDATES:
                respond({"jsonrpc": "2.0", "method": "session/update",
                         "params": {"sessionId": "sess-1", **u}})
            respond({"jsonrpc": "2.0", "id": rid,
                     "result": {"sessionId": "sess-1"}})
        elif method == "session/prompt":
            prompt = ((msg.get("params") or {}).get("prompt") or [{}])[0].get("text", "")
            respond({"jsonrpc": "2.0", "method": "session/update",
                     "params": {"sessionId": "sess-1", "update": {
                         "sessionUpdate": "available_commands_update",
                         "availableCommands": [{"name": "review", "description": "Review code"}]}}})
            if prompt == "silent":
                continue
            if prompt == "plan-write":
                respond({"jsonrpc": "2.0", "id": 900, "method": "fs/write_text_file",
                         "params": {"path": "blocked.txt", "content": "blocked"}})
                answer = json.loads(sys.stdin.readline())
                if "error" not in answer:
                    raise RuntimeError("plan write unexpectedly approved")
            if prompt == "stress":
                for index in range(500):
                    respond({"jsonrpc": "2.0", "method": "session/update",
                             "params": {"sessionId": "sess-1", "update": {
                                 "sessionUpdate": "agent_message_chunk",
                                 "content": {"type": "text", "text": str(index) + ","}}}})
                respond({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
                continue
            respond({"jsonrpc": "2.0", "method": "session/update",
                     "params": {
                         "sessionId": "sess-1",
                         "update": {"sessionUpdate": "agent_message_chunk",
                                    "content": {"type": "text",
                                                "text": "REPORTS" if "Verify claims against the workspace" in prompt else "PONG"}}}})
            respond({"jsonrpc": "2.0", "id": rid,
                     "result": {"stopReason": "end_turn"}})
        elif method == "session/set_config_option":
            config_id = (msg.get("params") or {}).get("configId")
            value = (msg.get("params") or {}).get("value")
            if not isinstance(value, str):
                respond({"jsonrpc": "2.0", "id": rid,
                         "error": {"code": -32602, "message": "Invalid params"}})
                continue
            CONFIG.add(config_id)
            respond({"jsonrpc": "2.0", "id": rid,
                     "result": [{"id": item, "currentValue": value} for item in sorted(CONFIG)]})
        elif method == "session/cancel":
            pass
        elif method == "session/close":
            respond({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            if rid is not None:
                respond({"jsonrpc": "2.0", "id": rid,
                         "error": {"code": -32601,
                                   "message": "fake: not implemented: "
                                   + str(method)}})
    '''
)


def _install_fake_acp(tmp_path: Path) -> Path:
    bin_path = tmp_path / "fake-acp.py"
    bin_path.write_text(_FAKE_ACP_SRC)
    bin_path.chmod(0o755)
    return bin_path


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bin_path = _install_fake_acp(tmp_path)
    # Use the same Python the test is running under.
    monkeypatch.setenv("GROKDECK_ACP_BIN", f"{sys.executable} {bin_path}")
    monkeypatch.setenv("GROKDECK_TESTING", "1")
    monkeypatch.setenv("GROKDECK_FAKE_STREAM", "1")
    monkeypatch.setenv("GROKDECK_SECRET_PATH", str(tmp_path / "key"))
    monkeypatch.setenv("GROKDECK_FAKE_DELAY", "0")
    monkeypatch.setenv("GROKDECK_HOME", str(tmp_path / "deck-home"))
    monkeypatch.setenv("GROKDECK_GROK_HOME", str(tmp_path / "grok-home"))
    monkeypatch.setenv("GROKDECK_GROK_BIN", str(tmp_path / "missing-grok"))
    grok_home = Path(os.environ["GROKDECK_GROK_HOME"])
    grok_home.mkdir(parents=True, exist_ok=True)
    (grok_home / "auth.json").write_text("placeholder-token")

    store = Store(tmp_path / "deck.db")
    store.set_settings({"buildCli": True, "allowTerminal": True})
    app = create_app(store)
    with TestClient(app) as test:
        yield test
    store.close()


def _assert_no_email_key(body: str) -> None:
    # JSON keys appear as '"email"' in serialized output. Substring check is
    # strict enough to catch a leak without false positives on values.
    assert '"email"' not in body


def test_build_sessions_list_returns_two_sessions(client: TestClient):
    resp = client.get("/api/build/sessions")
    assert resp.status_code == 200
    body = resp.json()
    sessions = body["sessions"]
    assert len(sessions) == 2
    assert sessions[0]["id"] == "sess-1"
    assert sessions[0]["title"] == "First session"
    assert sessions[0]["cwd"] == "/tmp/grokdeck-scratch-1"
    assert sessions[1]["id"] == "sess-2"
    assert sessions[1]["title"] == "Second session"
    assert isinstance(body["signedIn"], bool)
    _assert_no_email_key(resp.text)


def test_build_setting_blocks_new_and_existing_session_turns(client: TestClient):
    cwd = str(client.app.state.store.path.parent)
    client.put("/api/settings", json={"buildCli": False})
    assert client.post("/api/build/sessions", json={"cwd": cwd}).status_code == 409
    assert client.post("/api/build/sessions/sess-1/load", json={"cwd": cwd}).status_code == 409
    assert client.post("/api/build/sessions/sess-1/prompt", json={"text": "ping"}).status_code == 409


def test_build_policy_restricts_files_terminal_and_autoapproval():
    from sidecar.acp_agent import AcpAgent

    async def approved(policy: dict, mode: str, operation: str) -> bool:
        agent = AcpAgent(lambda: policy)
        return await agent._approval({"mode": mode}, {"operation": operation})

    assert asyncio.run(approved({"allowFs": False}, "full-auto", "read")) is False
    assert asyncio.run(approved({"allowFs": False}, "full-auto", "write")) is False
    assert asyncio.run(approved({"allowTerminal": False}, "full-auto", "terminal")) is False
    assert asyncio.run(approved({"autoApprove": True}, "ask", "write")) is True
    assert asyncio.run(approved({"autoApprove": True}, "plan", "write")) is False


def test_build_sessions_load_returns_roles_and_verbose(client: TestClient):
    resp = client.post("/api/build/sessions/sess-1/load",
                       json={"cwd": str(client.app.state.store.path.parent)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["session"]["id"] == "sess-1"
    assert body["commands"] == [{"name": "review", "description": "Review code"}]
    assert "not for the UI" not in resp.text
    roles = [m["role"] for m in body["messages"]]
    assert "user" in roles
    assert "assistant" in roles
    # Public verbose field is populated for at least one message.
    non_empty = [m for m in body["messages"] if m.get("verbose")]
    assert non_empty
    # Tool chip shape: id, name, path, readOnly, status.
    assistant = next(m for m in body["messages"] if m["role"] == "assistant")
    assert any(t.get("name") == "echo" for t in assistant["tools"])
    _assert_no_email_key(resp.text)


def test_build_sessions_prompt_streams_token_and_done(client: TestClient):
    cwd = str(client.app.state.store.path.parent)
    assert client.post("/api/build/sessions/sess-1/load", json={"cwd": cwd}).status_code == 200
    payload = {"text": "ping the agent", "cwd": cwd}
    with client.stream("POST", "/api/build/sessions/sess-1/prompt", json=payload) as response:
        assert response.status_code == 200
        raw = "".join(response.iter_text())
    assert "event: token" in raw
    assert "event: command" in raw
    assert "event: done" in raw
    # The prompt text is never echoed back.
    assert "ping the agent" not in raw
    _assert_no_email_key(raw)


def test_build_prompt_keeps_a_large_stream_complete(client: TestClient):
    cwd = str(client.app.state.store.path.parent)
    assert client.post("/api/build/sessions/sess-1/load", json={"cwd": cwd}).status_code == 200
    with client.stream("POST", "/api/build/sessions/sess-1/prompt", json={"text": "stress"}) as response:
        raw = "".join(response.iter_text())
    assert raw.count("event: token") == 500
    assert '"text": "0,"' in raw and '"text": "499,"' in raw
    assert raw.count("event: done") == 1


def test_abandoned_build_prompt_does_not_leak_into_next_turn(client: TestClient, tmp_path: Path):
    from sidecar.acp_agent import AcpAgent

    async def exercise():
        agent = AcpAgent()
        try:
            opened = await agent.open_session(str(tmp_path))
            session_id = opened["id"]
            stream = agent.prompt(session_id, "silent")
            first = await asyncio.wait_for(anext(stream), 3)
            assert first["type"] == "command"
            await stream.aclose()
            assert not agent._pending
            assert agent._stream_queue is None
            assert agent._reader_task is None

            await agent.load_session(session_id, str(tmp_path))
            events = [event async for event in agent.prompt(session_id, "ping")]
            assert any(event.get("type") == "token" and event.get("text") == "PONG" for event in events)
            assert events[-1]["type"] == "done"
        finally:
            await agent.close()

    asyncio.run(exercise())


def test_build_prompt_reports_unresponsive_cli(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from sidecar import acp_agent

    monkeypatch.setattr(acp_agent, "PROMPT_IDLE_TIMEOUT", 0.1)
    cwd = str(client.app.state.store.path.parent)
    assert client.post("/api/build/sessions/sess-1/load", json={"cwd": cwd}).status_code == 200
    with client.stream("POST", "/api/build/sessions/sess-1/prompt", json={"text": "silent"}) as response:
        raw = "".join(response.iter_text())
    assert "event: error" in raw
    assert "Grok Build did not respond" in raw
    assert "event: done" not in raw


def test_build_session_applies_documented_config_options(client: TestClient):
    opened = client.post("/api/build/sessions", json={"cwd": str(client.app.state.store.path.parent)})
    assert opened.status_code == 200
    assert opened.json()["effort"] == "low"
    assert opened.json()["commands"] == [{"name": "review", "description": "Review code"}]
    assert opened.json()["efforts"] == ["high", "low"]
    session_id = opened.json()["id"]
    with client.stream("POST", f"/api/build/sessions/{session_id}/prompt", json={
        "text": "configured", "model": "grok-4.7", "effort": "high",
    }) as response:
        assert response.status_code == 200
        raw = "".join(response.iter_text())
    assert "event: token" in raw
    assert "event: done" in raw


def test_build_prompt_passes_provider_reports_to_cli(client: TestClient):
    opened = client.post("/api/build/sessions", json={"cwd": str(client.app.state.store.path.parent)})
    assert opened.status_code == 200
    session_id = opened.json()["id"]
    with client.stream("POST", f"/api/build/sessions/{session_id}/prompt", json={
        "text": "review this", "workerContext": "[deepseek · deepseek-chat]\nCheck the parser.",
    }) as response:
        assert response.status_code == 200
        raw = "".join(response.iter_text())
    assert "REPORTS" in raw
    assert "Check the parser" not in raw


def test_plan_mode_denies_write_requests(client: TestClient):
    opened = client.post("/api/build/sessions", json={
        "cwd": str(client.app.state.store.path.parent), "mode": "plan",
    })
    assert opened.status_code == 200
    session_id = opened.json()["id"]
    with client.stream("POST", f"/api/build/sessions/{session_id}/prompt", json={
        "text": "plan-write", "mode": "plan",
    }) as response:
        assert response.status_code == 200
        raw = "".join(response.iter_text())
    assert "event: token" in raw
    assert "event: error" not in raw
    assert not (client.app.state.store.path.parent / "blocked.txt").exists()


def test_build_sessions_reject_unknown_mode(client: TestClient):
    response = client.post("/api/build/sessions", json={
        "cwd": str(client.app.state.store.path.parent), "mode": "turbo",
    })
    assert response.status_code == 400


def test_build_sessions_create_rejects_bad_cwd(client: TestClient):
    bad = client.post("/api/build/sessions", json={"cwd": "/no/such/dir/anywhere"})
    assert bad.status_code == 400


def test_build_sessions_stop_and_permission_acks(client: TestClient):
    stop = client.post("/api/build/sessions/sess-1/stop")
    assert stop.status_code == 200
    assert stop.json() == {"ok": True}
    perm = client.post("/api/build/sessions/sess-1/permission",
                       json={"id": 999, "allow": True})
    assert perm.status_code == 200
    assert perm.json() == {"ok": True}


def test_build_files_uses_local_git(client: TestClient, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # git needs identity + an initial commit before "diff" shows changes.
    subprocess.run(["git", "init", str(repo)], capture_output=True, timeout=10)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "x@y.z"],
                   capture_output=True, timeout=10)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "x"],
                   capture_output=True, timeout=10)
    (repo / "hello.txt").write_text("hi\n")
    subprocess.run(["git", "-C", str(repo), "add", "hello.txt"],
                   capture_output=True, timeout=10)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   capture_output=True, timeout=10)
    (repo / "hello.txt").write_text("hi v2\n")

    resp = client.get("/api/build/files", params={"cwd": str(repo)})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["files"], list)
    assert isinstance(body["diff"], str)
    assert any(item["path"] == "hello.txt" for item in body["files"])
    assert "hi v2" in body["diff"]


def test_build_sse_preserves_large_unicode_deltas_and_marks_metadata(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import json

    from sidecar.grokdeck_server import _build_sessions_prompt_stream

    text = "α😀\n" * 7000
    reasoning = "🧠reason\n" * 3000

    async def prompt(*_args):
        yield {"type": "token", "text": text, "verbose": text, "detail": "x" * 9000}
        yield {"type": "reasoning", "text": reasoning}
        yield {"type": "done", "stopReason": "end_turn"}

    monkeypatch.setattr(client.app.state.acp_agent, "prompt", prompt)

    async def collect():
        return [event async for event in _build_sessions_prompt_stream(client.app, "sess-1", "ignored")]

    raw = asyncio.run(collect())
    events = [(entry.splitlines()[0].removeprefix("event: "), json.loads(entry.splitlines()[1].removeprefix("data: "))) for entry in raw]
    tokens = [data for kind, data in events if kind == "token"]
    thoughts = [data for kind, data in events if kind == "reasoning"]
    assert len(tokens) > 1 and len(thoughts) > 1
    assert "".join(data["text"] for data in tokens) == text
    assert "".join(data["verbose"] for data in tokens) == text
    assert "".join(data["text"] for data in thoughts) == reasoning
    assert all(len(value) <= 8000 for _, data in events for value in data.values() if isinstance(value, str))
    assert tokens[0]["detail"].endswith("… [truncated]")
    assert [kind for kind, _ in events] == ["token"] * len(tokens) + ["reasoning"] * len(thoughts) + ["done"]
