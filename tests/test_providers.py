"""Provider configuration stays local and validates before any network call."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path

import httpx
import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sidecar import providers
from sidecar.grokdeck_server import create_app
from sidecar.store import Store


def test_preset_provider_configures_models_and_resolves():
    store = Store(":memory:")
    saved = providers.upsert(store, {"id": "deepseek", "models": ["deepseek-reasoner"]})
    provider, model = providers.resolve(store, "deepseek:deepseek-reasoner")
    assert saved["apiBase"] == "https://api.deepseek.com/v1"
    assert provider["id"] == "deepseek"
    assert model == "deepseek-reasoner"
    store.close()


def test_mistral_preset_uses_compatible_effort(monkeypatch):
    store = Store(":memory:")
    saved = providers.upsert(store, {"id": "mistral", "models": ["mistral-large-4"]})
    assert saved["apiBase"] == "https://api.mistral.ai/v1"
    provider, model = providers.resolve(store, "mistral:mistral-large-4")
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n')

    real_client = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: real_client(transport=httpx.MockTransport(respond)))

    async def consume(effort):
        return [event async for event in providers._openai_stream(provider, "unused", model, [], effort, lambda: False)]

    assert asyncio.run(consume("high"))[0] == {"type": "token", "text": "ok"}
    assert asyncio.run(consume("medium"))[0] == {"type": "token", "text": "ok"}
    assert [request["reasoning_effort"] for request in requests] == ["high", "none"]
    store.close()


def test_provider_transport_failure_returns_a_stream_error(monkeypatch):
    provider = providers.validate({"id": "minimax", "models": ["MiniMax-M2.5"]})

    def fail(_request):
        raise httpx.ConnectError("private network detail")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: real_client(transport=httpx.MockTransport(fail)))
    monkeypatch.setattr(providers.secrets, "get_provider_key", lambda _id: "fixture-only")

    async def consume():
        return [event async for event in providers.stream_chat(provider, "MiniMax-M2.5", [{"role": "user", "content": "ping"}], "low", lambda: False)]

    events = asyncio.run(consume())
    assert events == [{"type": "error", "message": "Could not reach the provider. Check its connection and try again."}]


def test_codex_start_failure_returns_a_stream_error(monkeypatch):
    monkeypatch.setattr(providers.shutil, "which", lambda _name: "/fixture/codex")

    async def fail(*_args, **_kwargs):
        raise OSError("private subprocess detail")

    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", fail)

    async def consume():
        return [event async for event in providers.stream_chat(
            {"id": "codex", "protocol": "codex"}, "fixture-model",
            [{"role": "user", "content": "ping"}], "low", lambda: False,
        )]

    assert asyncio.run(consume()) == [{
        "type": "error", "message": "Codex could not start. Check codex login and try again.",
    }]


def test_codex_stop_kills_a_stalled_subprocess(tmp_path, monkeypatch):
    fake = tmp_path / "fake-codex"
    fake.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n", encoding="utf-8")
    fake.chmod(0o700)
    monkeypatch.setattr(providers.shutil, "which", lambda _name: str(fake))

    async def exercise():
        stop = asyncio.Event()

        async def consume():
            return [event async for event in providers.stream_chat(
                {"id": "codex", "protocol": "codex"}, "fixture-model",
                [{"role": "user", "content": "ping"}], "low", stop.is_set,
            )]

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.2)
        stop.set()
        return await asyncio.wait_for(task, 2)

    assert asyncio.run(exercise()) == [{"type": "done", "stop": "stopped"}]


def test_openai_adapters_handle_parallel_streams_and_empty_replies(monkeypatch):
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append((request.url.host, payload["model"]))
        if payload["model"] == "empty-model":
            return httpx.Response(200, text='data: [DONE]\n\n')
        if payload["model"] == "closed-model":
            return httpx.Response(200, text="")
        if payload["model"] == "reasoning-only-model":
            return httpx.Response(200, text=(
                'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n\n'
                'data: [DONE]\n\n'
            ))
        return httpx.Response(200, text=(
            'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            'data: [DONE]\n\n'
        ))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: real_client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(providers.secrets, "get_provider_key", lambda _id: "fixture-only")
    names = ("mistral", "openai", "deepseek", "minimax", "google")

    async def one(name, model):
        provider = providers.validate({"id": name, "models": [model]})
        return [event async for event in providers.stream_chat(provider, model, [{"role": "user", "content": "ping"}], None, lambda: False)]

    async def exercise():
        return await asyncio.gather(
            *(one(name, "fixture-model") for name in names),
            one("minimax", "empty-model"), one("minimax", "closed-model"), one("minimax", "reasoning-only-model"),
        )

    results = asyncio.run(exercise())
    assert all(result == [{"type": "token", "text": "ok"}, {"type": "done", "stop": "stop"}] for result in results[:-3])
    assert results[-3] == results[-2] == [{"type": "error", "message": "The provider returned no answer."}]
    assert results[-1] == [{"type": "reasoning", "text": "thinking"}, {"type": "error", "message": "The provider returned no answer."}]
    assert len(calls) == 8


def test_stalled_provider_stream_stops_promptly(monkeypatch):
    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            pass

    real_client = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: real_client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, stream=Stalled())),
    ))
    monkeypatch.setattr(providers.secrets, "get_provider_key", lambda _id: "fixture-only")
    provider = providers.validate({"id": "google", "models": ["fixture-model"]})

    async def exercise():
        stop = asyncio.Event()

        async def request():
            return [event async for event in providers.stream_chat(
                provider, "fixture-model", [{"role": "user", "content": "ping"}], None, stop.is_set,
            )]

        task = asyncio.create_task(request())
        await asyncio.sleep(0.15)
        stop.set()
        return await asyncio.wait_for(task, 1)

    assert asyncio.run(exercise()) == [{"type": "done", "stop": "stopped"}]


def test_anthropic_adapter_parses_text_without_a_live_request(monkeypatch):
    provider = providers.validate({"id": "claude", "models": ["fixture-model"]})
    response = (
        'event: content_block_delta\ndata: {"delta":{"type":"text_delta","text":"hello"}}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: real_client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=response)),
    ))
    monkeypatch.setattr(providers.secrets, "get_provider_key", lambda _id: "fixture-only")

    async def consume():
        return [event async for event in providers.stream_chat(
            provider, "fixture-model", [{"role": "user", "content": "ping"}], None, lambda: False,
        )]

    assert asyncio.run(consume()) == [{"type": "token", "text": "hello"}, {"type": "done", "stop": "stop"}]


def test_provider_rejects_remote_http_and_unknown_model():
    store = Store(":memory:")
    try:
        providers.upsert(store, {"id": "unsafe", "apiBase": "http://example.com/v1", "models": ["example"]})
    except ValueError as error:
        assert "HTTPS" in str(error)
    else:
        raise AssertionError("remote HTTP endpoint was accepted")
    try:
        providers.resolve(store, "grok:unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown model was accepted")
    store.close()


def test_custom_provider_cannot_replace_codex_config():
    store = Store(":memory:")
    providers.upsert(store, {"id": "codex", "models": ["gpt-6-luna"]})
    with pytest.raises(ValueError, match="built-in Codex"):
        providers.upsert(store, {"id": "other", "protocol": "codex", "models": ["fixture-model"]})
    with pytest.raises(ValueError, match="requires the Codex protocol"):
        providers.upsert(store, {"id": "codex", "protocol": "openai", "models": ["fixture-model"]})
    assert providers.resolve(store, "codex:gpt-6-luna")[1] == "gpt-6-luna"
    assert [item["id"] for item in store.get_settings()["providers"]] == ["codex"]
    store.close()


def test_mixed_provider_streams_isolate_parallel_results_and_failures(monkeypatch):
    names = ("mistral", "openai", "deepseek", "zai", "minimax", "google", "ollama", "claude")
    requests = []

    async def respond(request):
        payload = json.loads(request.content)
        model = payload["model"]
        requests.append((request.url.host, model))
        if int(model.removeprefix("stress-")) % 10 == 0:
            return httpx.Response(429, json={"error": {"message": "Rate limit reached"}})
        if request.url.path.endswith("/messages"):
            body = f'event: content_block_delta\ndata: {{"delta":{{"type":"text_delta","text":"{model}"}}}}\n\n'
            body += 'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        else:
            body = f'data: {{"choices":[{{"delta":{{"content":"{model}"}}}}]}}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: real_client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(providers.secrets, "get_provider_key", lambda _id: "fixture-only")

    async def one(name, index):
        model = f"stress-{index}"
        provider = providers.validate({"id": name, "models": [model]})
        return [event async for event in providers.stream_chat(
            provider, model, [{"role": "user", "content": "ping"}], None, lambda: False,
        )]

    async def exercise():
        return await asyncio.gather(*(one(names[i % len(names)], i) for i in range(40)))

    results = asyncio.run(exercise())
    assert len(requests) == 40
    for index, events in enumerate(results):
        if index % 10 == 0:
            assert events == [{"type": "error", "message": "Rate limit reached"}]
        else:
            assert events == [{"type": "token", "text": f"stress-{index}"}, {"type": "done", "stop": "stop"}]


def test_worker_selection_honors_a_named_provider_and_stays_context_only(monkeypatch):
    store = Store(":memory:")
    providers.upsert(store, {"id": "deepseek", "models": ["deepseek-reasoner"]})
    monkeypatch.setattr(providers, "list_providers", lambda _store: [{
        "id": "deepseek", "configured": True, "keyRequired": True, "keyConfigured": True,
        "models": ["deepseek-reasoner"],
    }])
    assert providers.worker_models(store, ["deepseek"], "strong", 1) == ["deepseek:deepseek-reasoner"]

    async def fake_stream(provider, model, messages, effort, cancel):
        assert provider["id"] == "deepseek"
        assert model == "deepseek-reasoner"
        assert effort == "high"
        assert "Do not use tools" in messages[0]["content"]
        yield {"type": "token", "text": "checked"}
        yield {"type": "done", "stop": "stop"}

    monkeypatch.setattr(providers, "stream_chat", fake_stream)
    result = asyncio.run(providers.run_worker(store, "deepseek:deepseek-reasoner", "Review this function", "high"))
    assert result == {"provider": "deepseek", "model": "deepseek-reasoner", "status": "completed", "text": "checked", "reasoning": ""}
    store.close()


def test_worker_selection_uses_strong_named_models_and_distinct_auto_providers(monkeypatch):
    store = Store(":memory:")
    rows = [
        {"id": "deepseek", "models": ["deepseek-chat", "deepseek-reasoner"]},
        {"id": "mistral", "models": ["mistral-small", "mistral-large-4"]},
        {"id": "google", "models": ["gemini-flash", "gemini-pro"]},
    ]
    for row in rows:
        providers.upsert(store, row)
    monkeypatch.setattr(providers.secrets, "provider_configured", lambda _id: True)
    assert providers.worker_models(store, ["deepseek", "mistral", "google"], "strong", 3) == [
        "deepseek:deepseek-reasoner", "mistral:mistral-large-4", "google:gemini-pro",
    ]
    automatic = providers.worker_models(store, ["auto"], "strong", 3)
    assert len(automatic) == 3
    assert {reference.partition(":")[0] for reference in automatic} == {"deepseek", "mistral", "google"}
    assert providers.worker_models(store, ["deepseek:deepseek-chat"], "strong", 3) == ["deepseek:deepseek-chat"]
    store.close()


def test_models_need_a_key_when_the_provider_requires_one(monkeypatch):
    store = Store(":memory:")
    providers.upsert(store, {"id": "deepseek", "models": ["deepseek-reasoner"]})
    monkeypatch.setattr(providers.secrets, "provider_configured", lambda _provider_id: False)
    assert [row["id"] for row in providers.provider_models(store)] == ["grok:subscription"]
    store.close()


@pytest.mark.integration
def test_provider_and_worker_routes_keep_keys_out_of_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("GROKDECK_TESTING", "1")
    monkeypatch.setenv("GROKDECK_SECRET_PATH", str(tmp_path / "key"))
    store = Store(tmp_path / "deck.db")
    app = create_app(store)

    async def fake_worker(_store, reference, task, effort):
        assert reference == "deepseek:deepseek-reasoner"
        assert task == "Review this plan"
        assert effort == "high"
        return {"provider": "deepseek", "model": "deepseek-reasoner", "status": "completed", "text": "looks sound", "reasoning": ""}

    monkeypatch.setattr(providers, "run_worker", fake_worker)

    async def exercise():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        runner = asyncio.create_task(server.serve())
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                saved = await client.put("/api/providers", json={
                    "id": "deepseek", "models": ["deepseek-reasoner"], "key": "secret-never-in-settings",
                })
                assert saved.status_code == 200
                assert "secret-never-in-settings" not in saved.text
                settings = await client.put("/api/settings", json={"subagentQuality": "strong", "subagentWorkers": 1})
                assert settings.status_code == 200
                delegated = await client.post("/api/agents/delegate", json={
                    "task": "Review this plan", "models": ["deepseek"], "effort": "high",
                })
                assert delegated.status_code == 200
                assert delegated.json()["results"][0]["text"] == "looks sound"
        finally:
            server.should_exit = True
            await runner

    asyncio.run(exercise())
    assert "secret-never-in-settings" not in str(store.get_settings())
    store.close()
