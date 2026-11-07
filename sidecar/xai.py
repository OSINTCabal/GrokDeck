"""xAI API client. OpenAI-compatible chat completions.

The key is passed in memory and sent only as an Authorization header.
Nothing in this module writes the key or the prompt to a log.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from sidecar.stream_parse import deltas, take_events

API = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1")

# Used only when /v1/models cannot be called. Marked fallback by the server.
FALLBACK_MODES = [
    {"id": "fast", "label": "Fast", "model": "grok-4-fast", "effort": None, "fallback": True},
    {"id": "auto", "label": "Auto", "model": "grok-4", "effort": None, "fallback": True},
    {"id": "expert", "label": "Expert", "model": "grok-4", "effort": "high", "fallback": True},
    {"id": "heavy", "label": "Heavy", "model": "grok-4", "effort": "high", "fallback": True},
]


def modes_from_catalog(model_ids: list[str]) -> list[dict[str, Any]]:
    """Pick a live model id for each composer mode. Falls back per mode."""
    ids = [m for m in model_ids if m]
    fast = _pick(ids, ("fast", "mini", "non-reasoning"))
    heavy = _pick(ids, ("heavy",))
    expert = _pick(ids, ("reasoning", "expert")) or _pick(ids, ("grok-4", "grok-3"))
    auto = _pick(ids, ("grok-4", "grok-3")) or expert
    return [
        {"id": "fast", "label": "Fast", "model": fast or _fallback("fast"), "effort": None, "fallback": fast is None},
        {"id": "auto", "label": "Auto", "model": auto or _fallback("auto"), "effort": None, "fallback": auto is None},
        {"id": "expert", "label": "Expert", "model": expert or _fallback("expert"), "effort": "high", "fallback": expert is None},
        {"id": "heavy", "label": "Heavy", "model": heavy or _fallback("heavy"), "effort": "high", "fallback": heavy is None},
    ]


def _fallback(mode: str) -> str:
    return next(item["model"] for item in FALLBACK_MODES if item["id"] == mode)


def _pick(ids: list[str], needles: tuple[str, ...]) -> str | None:
    for needle in needles:
        for model_id in ids:
            if needle in model_id.lower():
                return model_id
    return None


async def list_model_ids(key: str) -> list[str]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{API}/models", headers={"Authorization": f"Bearer {key}"})
    if resp.status_code >= 400:
        raise RuntimeError(f"model list returned {resp.status_code}")
    data = resp.json()
    rows = data.get("data") or data.get("models") or []
    out = []
    for row in rows:
        if isinstance(row, str):
            out.append(row)
        elif isinstance(row, dict) and row.get("id"):
            out.append(str(row["id"]))
    return out


async def stream_chat(
    key: str,
    model: str,
    messages: list[dict[str, Any]],
    *,
    effort: str | None = None,
    search: bool = False,
    cancel_check: Any = None,
) -> AsyncIterator[dict[str, Any]]:
    payload: dict[str, Any] = {"model": model, "stream": True, "messages": messages}
    if effort:
        payload["reasoning_effort"] = effort
    if search:
        payload["search_parameters"] = {"mode": "auto", "return_citations": True}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(300.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", f"{API}/chat/completions", headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                message = _safe_error(body, key)
                yield {"type": "error", "message": message or f"xAI returned {resp.status_code}"}
                return
            buf = ""
            async for chunk in resp.aiter_text():
                if cancel_check is not None and cancel_check():
                    yield {"type": "done", "stop": "stopped"}
                    return
                events, buf = take_events(buf + chunk)
                for event in events:
                    parsed = deltas(event)
                    if parsed.get("error"):
                        text = str(parsed["error"])
                        if key and key in text:
                            text = "the upstream request was rejected"
                        yield {"type": "error", "message": text}
                        return
                    if parsed["text"]:
                        yield {"type": "token", "text": parsed["text"]}
                    if parsed["reasoning"]:
                        yield {"type": "reasoning", "text": parsed["reasoning"]}
                    for url in parsed["citations"]:
                        yield {"type": "citation", "url": url}
                    if parsed["done"]:
                        yield {"type": "done", "stop": parsed.get("finish") or "stop"}
                        return
            if buf.strip():
                events, _rest = take_events(buf + "\n\n")
                for event in events:
                    parsed = deltas(event)
                    if parsed["text"]:
                        yield {"type": "token", "text": parsed["text"]}
            yield {"type": "done", "stop": "stop"}


def _safe_error(body: bytes, key: str) -> str:
    text = body.decode("utf-8", "replace")[:300]
    if key and key in text:
        return ""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
        msg = (data.get("error") or {}).get("message")
        if isinstance(msg, str) and key not in msg:
            return msg[:300]
    except (json.JSONDecodeError, UnicodeError):
        pass
    return ""
