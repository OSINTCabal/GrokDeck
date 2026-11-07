"""SSE parser tests. No network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sidecar.stream_parse import deltas, take_events  # noqa: E402
from sidecar.xai import modes_from_catalog  # noqa: E402


def test_events_split_across_chunks():
    one = 'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
    two = 'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    cut = len(one) + 8
    blob = one + two
    first, rest = take_events(blob[:cut])
    second, tail = take_events(rest + blob[cut:])
    assert tail == ""
    texts = []
    for event in first + second:
        texts.append(deltas(event)["text"])
    assert texts == ["Hel", "lo"]


def test_done_sentinel_and_reasoning_and_citation():
    payload = {
        "citations": ["https://example.com/a"],
        "choices": [{
            "delta": {"reasoning_content": "think", "content": ""},
            "finish_reason": "stop",
        }],
    }
    block = "data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n"
    events, rest = take_events(block)
    assert rest == ""
    parsed = [deltas(event) for event in events]
    assert parsed[0]["reasoning"] == "think"
    assert parsed[0]["citations"] == ["https://example.com/a"]
    assert parsed[0]["done"] is True
    assert parsed[1]["done"] is True


def test_error_payload_is_short():
    block = 'data: {"error":{"message":"nope"}}\n\n'
    events, _rest = take_events(block)
    assert deltas(events[0])["error"] == "nope"


def test_catalog_prefers_live_ids():
    modes = modes_from_catalog(["grok-4", "grok-4-fast", "grok-4-heavy"])
    by_id = {mode["id"]: mode["model"] for mode in modes}
    assert by_id["fast"] == "grok-4-fast"
    assert by_id["heavy"] == "grok-4-heavy"
    assert by_id["auto"] == "grok-4"
    assert all(mode["fallback"] is False for mode in modes)
