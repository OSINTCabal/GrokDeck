"""Parse OpenAI-style chat completion SSE frames.

Pure functions. No network, no logging of payload text by this module.
"""

from __future__ import annotations

import json
from typing import Any


def take_events(buffer: str) -> tuple[list[dict[str, Any]], str]:
    """Split a text buffer into complete SSE events and the leftover tail.

    Events are separated by a blank line. A chunk may end mid-event; that
    tail is returned unchanged so the caller can prepend the next read.
    """
    parts = buffer.split("\n\n")
    rest = parts.pop() if parts else ""
    events: list[dict[str, Any]] = []
    for part in parts:
        event = _one(part)
        if event is not None:
            events.append(event)
    return events, rest


def _one(block: str) -> dict[str, Any] | None:
    name = "message"
    data_lines: list[str] = []
    for raw in block.split("\n"):
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return None
    data = "\n".join(data_lines)
    if data == "[DONE]":
        return {"kind": "done", "event": name}
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return {"kind": "text", "event": name, "text": data}
    if not isinstance(payload, dict):
        return None
    payload["kind"] = "json"
    payload["event"] = name
    return payload


def deltas(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull text, reasoning, and citation URLs out of one completion chunk."""
    if payload.get("kind") == "done":
        return {"done": True, "text": "", "reasoning": "", "citations": []}
    text = ""
    reasoning = ""
    citations: list[str] = []
    for item in payload.get("citations") or []:
        if isinstance(item, str):
            citations.append(item)
        elif isinstance(item, dict) and item.get("url"):
            citations.append(str(item["url"]))
    choices = payload.get("choices") or []
    if choices:
        choice = choices[0] or {}
        delta = choice.get("delta") or {}
        if isinstance(delta, dict):
            bit = delta.get("content")
            if isinstance(bit, str):
                text = bit
            reason = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reason, str):
                reasoning = reason
            for ann in delta.get("annotations") or []:
                if isinstance(ann, dict):
                    url = (ann.get("url_citation") or {}).get("url") or ann.get("url")
                    if url:
                        citations.append(str(url))
        if choice.get("finish_reason"):
            return {
                "done": True,
                "text": text,
                "reasoning": reasoning,
                "citations": citations,
                "finish": choice.get("finish_reason"),
            }
    err = payload.get("error")
    if isinstance(err, dict) and err.get("message"):
        return {"done": True, "error": str(err["message"])[:300], "text": "", "reasoning": "", "citations": []}
    return {"done": False, "text": text, "reasoning": reasoning, "citations": citations}
