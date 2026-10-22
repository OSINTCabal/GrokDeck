"""Configured text providers for GrokDeck's local-model workspace.

Provider definitions live in the local database; credentials stay in the
system keyring.  The real Grok web workspace remains separate and uses its
own browser session.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from sidecar import secrets
from sidecar.stream_parse import take_events

_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_MODEL = re.compile(r"^[^\s]{1,160}$")
_PROTOCOLS = {"openai", "anthropic", "codex"}

PRESETS: tuple[dict[str, Any], ...] = (
    {"id": "codex", "label": "Codex / ChatGPT", "protocol": "codex", "apiBase": "", "models": ["gpt-5.4"], "keyRequired": False},
    {"id": "mistral", "label": "Mistral AI", "protocol": "openai", "apiBase": "https://api.mistral.ai/v1", "models": ["mistral-large-4"], "keyRequired": True},
    {"id": "openai", "label": "OpenAI API", "protocol": "openai", "apiBase": "https://api.openai.com/v1", "models": ["gpt-5.4"], "keyRequired": True},
    {"id": "claude", "label": "Claude API", "protocol": "anthropic", "apiBase": "https://api.anthropic.com/v1", "models": ["claude-sonnet-4-6"], "keyRequired": True},
    {"id": "deepseek", "label": "DeepSeek API", "protocol": "openai", "apiBase": "https://api.deepseek.com/v1", "models": ["deepseek-chat"], "keyRequired": True},
    {"id": "zai", "label": "Z.ai API", "protocol": "openai", "apiBase": "https://api.z.ai/api/paas/v4", "models": ["glm-5"], "keyRequired": True},
    {"id": "minimax", "label": "MiniMax API", "protocol": "openai", "apiBase": "https://api.minimax.io/v1", "models": ["MiniMax-M2.5"], "keyRequired": True},
    {"id": "google", "label": "Google AI Studio", "protocol": "openai", "apiBase": "https://generativelanguage.googleapis.com/v1beta/openai", "models": ["gemini-3-pro-preview"], "keyRequired": True},
    {"id": "ollama", "label": "Ollama", "protocol": "openai", "apiBase": "http://127.0.0.1:11434/v1", "models": ["llama3.3"], "keyRequired": False},
)


def _preset(provider_id: str) -> dict[str, Any] | None:
    return next((dict(item) for item in PRESETS if item["id"] == provider_id), None)


def _safe_base(value: Any, *, required: bool) -> str:
    base = str(value or "").strip().rstrip("/")
    if not base and not required:
        return ""
    try:
        parsed = urlsplit(base)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        valid = parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password
        valid = valid and not parsed.query and not parsed.fragment and (parsed.scheme == "https" or loopback)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("API base must be HTTPS, or loopback HTTP, without credentials or query text.")
    return base


def _models(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("Models must be a list.")
    models = [str(item).strip() for item in value]
    if not models or len(models) > 24 or any(not _MODEL.fullmatch(item) for item in models):
        raise ValueError("Add one to 24 non-empty model IDs.")
    return list(dict.fromkeys(models))


def validate(provider: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(provider.get("id") or "").strip().lower()
    if not _ID.fullmatch(provider_id) or provider_id == "grok":
        raise ValueError("Provider ID must start with a lowercase letter and use letters, digits, or dashes.")
    preset = _preset(provider_id)
    protocol = str(provider.get("protocol") or (preset or {}).get("protocol") or "openai")
    if protocol not in _PROTOCOLS:
        raise ValueError("Provider protocol must be OpenAI-compatible, Anthropic, or Codex.")
    if provider_id == "codex" and protocol != "codex":
        raise ValueError("The built-in Codex provider requires the Codex protocol.")
    if protocol == "codex":
        if provider_id != "codex":
            raise ValueError("The Codex protocol is only available for the built-in Codex provider.")
        return {"id": "codex", "label": "Codex / ChatGPT", "protocol": "codex", "apiBase": "", "models": _models(provider.get("models") or (preset or {})["models"]), "keyRequired": False}
    label = str(provider.get("label") or (preset or {}).get("label") or provider_id).strip()[:80]
    if not label:
        raise ValueError("Provider label is required.")
    return {
        "id": provider_id,
        "label": label,
        "protocol": protocol,
        "apiBase": _safe_base(provider.get("apiBase") or (preset or {}).get("apiBase"), required=True),
        "models": _models(provider.get("models") or (preset or {}).get("models")),
        "keyRequired": bool((preset or {}).get("keyRequired", True)),
    }


def _stored(store: Any) -> dict[str, dict[str, Any]]:
    value = store.get_settings().get("providers") or []
    if not isinstance(value, list):
        return {}
    found: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            clean = validate(item)
        except ValueError:
            continue
        found[clean["id"]] = clean
    return found


def list_providers(store: Any) -> list[dict[str, Any]]:
    saved = _stored(store)
    rows = [{
        "id": "grok", "label": "Grok subscription", "protocol": "grok", "models": ["subscription"],
        "keyRequired": False, "configured": True, "keyConfigured": secrets.configured(),
    }]
    for preset in PRESETS:
        provider = saved.get(preset["id"])
        if provider is None:
            rows.append({**preset, "configured": False, "keyConfigured": False})
            continue
        rows.append({**provider, "configured": True, "keyConfigured": secrets.provider_configured(provider["id"])})
    for provider_id, provider in sorted(saved.items()):
        if _preset(provider_id) is None:
            rows.append({**provider, "configured": True, "keyConfigured": secrets.provider_configured(provider_id)})
    return rows


def upsert(store: Any, provider: dict[str, Any]) -> dict[str, Any]:
    clean = validate(provider)
    saved = _stored(store)
    saved[clean["id"]] = clean
    store.set_settings({"providers": list(saved.values())})
    return clean


def remove(store: Any, provider_id: str) -> bool:
    saved = _stored(store)
    if provider_id not in saved:
        return False
    saved.pop(provider_id)
    store.set_settings({"providers": list(saved.values())})
    secrets.clear_provider_key(provider_id)
    return True


def resolve(store: Any, reference: str) -> tuple[dict[str, Any], str]:
    provider_id, separator, model = str(reference or "").partition(":")
    if not separator or not provider_id or not model:
        raise ValueError("Choose a configured provider model.")
    if provider_id == "grok" and model == "subscription":
        return {"id": "grok", "protocol": "grok"}, model
    provider = _stored(store).get(provider_id)
    if provider is None or model not in provider["models"]:
        raise ValueError("That provider model is not configured.")
    return provider, model


def provider_models(store: Any) -> list[dict[str, str]]:
    rows = [{"id": "grok:subscription", "label": "Grok subscription", "provider": "grok", "model": "subscription"}]
    for provider in list_providers(store):
        if provider["id"] == "grok" or not provider.get("configured") or (provider.get("keyRequired") and not provider.get("keyConfigured")):
            continue
        for model in provider.get("models") or []:
            rows.append({"id": f"{provider['id']}:{model}", "label": f"{provider['label']} · {model}", "provider": provider["id"], "model": model})
    return rows


def worker_models(store: Any, requested: list[str], quality: str, limit: int) -> list[str]:
    """Choose configured, non-Grok models without silently substituting names."""
    if quality not in {"balanced", "strong"}:
        raise ValueError("Worker quality must be balanced or strong.")
    if not 1 <= limit <= 3:
        raise ValueError("Choose one to three workers.")
    available = [
        row for row in list_providers(store)
        if row.get("configured") and row["id"] != "grok" and (not row.get("keyRequired") or row.get("keyConfigured"))
    ]
    references = [f"{row['id']}:{model}" for row in available for model in row.get("models") or []]
    if not references:
        raise ValueError("Connect at least one provider with a usable key before delegating.")
    # shortcut: model-name hints are the only portable tier signal; exact model requests override them.
    def score(reference: str) -> tuple[int, int, str]:
        name = reference.partition(":")[2].lower()
        tier = (2 if any(hint in name for hint in ("reason", "large", "pro", "glm-5", "gpt-5", "gpt-6", "minimax-m2"))
                else 0 if any(hint in name for hint in ("mini", "small", "flash", "lite", "air")) else 1)
        return (-abs(tier - (2 if quality == "strong" else 1)), tier, reference)

    requested = [str(item).strip() for item in requested if str(item).strip()]
    if requested and requested != ["auto"]:
        selected: list[str] = []
        for item in requested:
            if ":" in item:
                provider, model = resolve(store, item)
                if provider["id"] == "grok":
                    raise ValueError("Grok subscription cannot be used as a background worker.")
                reference = f"{provider['id']}:{model}"
                if reference not in references:
                    raise ValueError(f"{item} is not ready for worker use.")
                selected.append(reference)
                continue
            found = max((reference for reference in references if reference.startswith(item.lower() + ":")),
                        key=score, default=None)
            if found is None:
                raise ValueError(f"{item} is not a configured provider.")
            selected.append(found)
        if len(set(selected)) != len(selected):
            raise ValueError("Duplicate workers would waste a request.")
        if len(selected) > limit:
            raise ValueError("Worker request exceeds the configured limit.")
        return selected

    best: dict[str, str] = {}
    for reference in references:
        provider_id = reference.partition(":")[0]
        if provider_id not in best or score(reference) > score(best[provider_id]):
            best[provider_id] = reference
    return sorted(best.values(), key=score, reverse=True)[:limit]


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return ""


def _safe_error(body: bytes, key: str) -> str:
    text = body.decode("utf-8", "replace")[:300]
    if key and key in text:
        return "The provider rejected this request."
    try:
        payload = json.loads(text)
        error = payload.get("error") or {}
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:300]
    except (json.JSONDecodeError, UnicodeError):
        pass
    return "The provider rejected this request."


async def _cancellable_chunks(response: httpx.Response, cancel: Any) -> AsyncIterator[str | None]:
    iterator = response.aiter_text()
    while True:
        read = asyncio.create_task(anext(iterator, None))
        try:
            while not read.done() and not cancel():
                await asyncio.wait({read}, timeout=0.1)
            if cancel():
                if not read.done():
                    read.cancel()
                    await asyncio.gather(read, return_exceptions=True)
                yield None
                return
            chunk = read.result()
            if chunk is None:
                return
            yield chunk
        finally:
            if not read.done():
                read.cancel()
                await asyncio.gather(read, return_exceptions=True)


async def _openai_stream(
    provider: dict[str, Any], key: str, model: str, messages: list[dict[str, Any]], effort: str | None, cancel: Any,
) -> AsyncIterator[dict[str, str]]:
    payload: dict[str, Any] = {"model": model, "stream": True, "messages": messages}
    if effort:
        payload["reasoning_effort"] = ("high" if effort in {"high", "xhigh"} else "none") if provider["id"] == "mistral" else effort
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    timeout = httpx.Timeout(300.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        async with client.stream("POST", provider["apiBase"] + "/chat/completions", headers=headers, json=payload) as response:
            if response.status_code >= 400:
                yield {"type": "error", "message": _safe_error(await response.aread(), key)}
                return
            buffer = ""
            async for chunk in _cancellable_chunks(response, cancel):
                if chunk is None:
                    yield {"type": "done", "stop": "stopped"}
                    return
                events, buffer = take_events(buffer + chunk)
                for event in events:
                    if event.get("kind") == "done":
                        yield {"type": "done", "stop": "stop"}
                        return
                    error = event.get("error")
                    if isinstance(error, dict):
                        yield {"type": "error", "message": str(error.get("message") or "The provider returned an error.")[:300]}
                        return
                    choice = (event.get("choices") or [{}])[0] or {}
                    delta = choice.get("delta") or {}
                    if isinstance(delta.get("content"), str):
                        yield {"type": "token", "text": delta["content"]}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if isinstance(reasoning, str):
                        yield {"type": "reasoning", "text": reasoning}
                    if choice.get("finish_reason"):
                        yield {"type": "done", "stop": str(choice["finish_reason"])}
                        return
    yield {"type": "done", "stop": "stop"}


async def _anthropic_stream(
    provider: dict[str, Any], key: str, model: str, messages: list[dict[str, Any]], cancel: Any,
) -> AsyncIterator[dict[str, str]]:
    system = "\n\n".join(_text_content(item.get("content")) for item in messages if item.get("role") == "system").strip()
    conversation = [
        {"role": item["role"], "content": _text_content(item.get("content"))}
        for item in messages if item.get("role") in {"user", "assistant"}
    ]
    payload: dict[str, Any] = {"model": model, "stream": True, "max_tokens": 4096, "messages": conversation}
    if system:
        payload["system"] = system
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    timeout = httpx.Timeout(300.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        async with client.stream("POST", provider["apiBase"] + "/messages", headers=headers, json=payload) as response:
            if response.status_code >= 400:
                yield {"type": "error", "message": _safe_error(await response.aread(), key)}
                return
            buffer = ""
            async for chunk in _cancellable_chunks(response, cancel):
                if chunk is None:
                    yield {"type": "done", "stop": "stopped"}
                    return
                events, buffer = take_events(buffer + chunk)
                for event in events:
                    if event.get("event") == "message_stop":
                        yield {"type": "done", "stop": "stop"}
                        return
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                        yield {"type": "token", "text": delta["text"]}
                    if delta.get("type") == "thinking_delta" and isinstance(delta.get("thinking"), str):
                        yield {"type": "reasoning", "text": delta["thinking"]}
                    if event.get("type") == "error":
                        yield {"type": "error", "message": str((event.get("error") or {}).get("message") or "The provider returned an error.")[:300]}
                        return
    yield {"type": "done", "stop": "stop"}


def _codex_prompt(messages: list[dict[str, Any]]) -> str:
    """Keep the CLI request self-contained and away from GrokDeck's source tree."""
    parts = []
    for item in messages:
        role = str(item.get("role") or "user")
        content = _text_content(item.get("content")).strip()
        if content:
            parts.append(f"{role}: {content}")
    return "\n\n".join(parts)[-30_000:]


async def _codex_stream(
    model: str, messages: list[dict[str, Any]], effort: str | None, cancel: Any,
) -> AsyncIterator[dict[str, str]]:
    executable = shutil.which("codex")
    if not executable:
        yield {"type": "error", "message": "Codex CLI was not found. Install it, then run codex login."}
        return
    workdir = Path(tempfile.mkdtemp(prefix="grokdeck-codex-"))
    argv = [executable, "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--model", model]
    if effort in {"low", "medium", "high", "xhigh"}:
        argv.extend(["--config", f'model_reasoning_effort="{effort}"'])
    argv.append("-")
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=workdir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(_codex_prompt(messages).encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        while True:
            if cancel():
                process.kill()
                await process.wait()
                yield {"type": "done", "stop": "stopped"}
                return
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=0.25)
            except TimeoutError:
                if process.returncode is not None:
                    break
                continue
            if not raw:
                break
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            item = event.get("item") if event.get("type") == "item.completed" else None
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text") or item.get("content")
                if isinstance(text, str) and text:
                    yield {"type": "token", "text": text}
        if process.returncode is None:
            await process.wait()
        if process.returncode:
            yield {"type": "error", "message": "Codex could not complete this chat. Check codex login and try again."}
            return
        yield {"type": "done", "stop": "stop"}
    except OSError:
        yield {"type": "error", "message": "Codex could not start. Check codex login and try again."}
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        shutil.rmtree(workdir, ignore_errors=True)


async def stream_chat(
    provider: dict[str, Any], model: str, messages: list[dict[str, Any]], effort: str | None, cancel: Any,
) -> AsyncIterator[dict[str, str]]:
    if provider["protocol"] == "codex":
        stream = _codex_stream(model, messages, effort, cancel)
    else:
        key = secrets.get_provider_key(provider["id"])
        if provider.get("keyRequired") and not key:
            yield {"type": "error", "message": f"Add an API key for {provider['label']} in Settings → Providers."}
            return
        stream = (_anthropic_stream(provider, key or "", model, messages, cancel)
                  if provider["protocol"] == "anthropic"
                  else _openai_stream(provider, key or "", model, messages, effort, cancel))
    answered = False
    try:
        async for event in stream:
            if event["type"] == "token" and event.get("text"):
                answered = True
            if event["type"] == "error":
                yield event
                return
            if event["type"] == "done" and event.get("stop") != "stopped" and not answered:
                yield {"type": "error", "message": "The provider returned no answer."}
                return
            yield event
            if event["type"] == "done":
                return
        if not answered:
            yield {"type": "error", "message": "The provider returned no answer."}
    except httpx.RequestError:
        yield {"type": "error", "message": "Could not reach the provider. Check its connection and try again."}


async def run_worker(store: Any, reference: str, task: str, effort: str | None) -> dict[str, str]:
    """One bounded context-only worker. It has no files, tools, or retry loop."""
    if not task.strip() or len(task) > 20_000:
        raise ValueError("Worker task must be between one and 20,000 characters.")
    provider, model = resolve(store, reference)
    if provider["id"] == "grok":
        raise ValueError("Grok subscription cannot be used as a background worker.")
    text, reasoning = "", ""
    messages = [
        {"role": "system", "content": "Complete this bounded task concisely. Return findings or a proposed approach. Do not use tools, read files, claim changes, expose credentials, or delegate."},
        {"role": "user", "content": task.strip()},
    ]
    try:
        async with asyncio.timeout(180):
            async for event in stream_chat(provider, model, messages, effort, lambda: False):
                if event["type"] == "token":
                    text = (text + event["text"])[:16_000]
                elif event["type"] == "reasoning":
                    reasoning = (reasoning + event["text"])[:4_000]
                elif event["type"] == "error":
                    return {"provider": provider["id"], "model": model, "status": "failed", "detail": event["message"]}
    except TimeoutError:
        return {"provider": provider["id"], "model": model, "status": "failed", "detail": "Worker reached the three-minute limit."}
    if not text.strip():
        return {"provider": provider["id"], "model": model, "status": "failed", "detail": "Worker stopped without a final reply."}
    return {"provider": provider["id"], "model": model, "status": "completed", "text": text, "reasoning": reasoning}
