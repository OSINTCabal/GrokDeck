"""Usage parsing stays local to numbers and plan labels. No network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sidecar import usage  # noqa: E402

_BILLING = {
    "config": {
        "currentPeriod": {
            "type": "USAGE_PERIOD_TYPE_WEEKLY",
            "start": "2026-10-08T05:25:15+00:00",
            "end": "2026-10-15T05:25:15+00:00",
        },
        "creditUsagePercent": 88.0,
        "onDemandCap": {"val": 0},
        "onDemandUsed": {"val": 0},
        "prepaidBalance": {"val": 0},
        "productUsage": [{"product": "GrokBuild", "usagePercent": 88.0}],
        "email": "hidden@example.com",
    },
    "email": "hidden@example.com",
}


def test_present_keeps_the_weekly_pool_and_drops_identity():
    body = usage.present(_BILLING, {
        "subscriptionTier": "GrokPro",
        "codingDataRetentionOptOut": True,
        "email": "hidden@example.com",
        "teamId": "team-should-not-leak",
    })
    text = json.dumps(body)
    assert "hidden@example.com" not in text
    assert "team-should-not-leak" not in text
    assert body["ok"] is True
    assert body["tier"] == "Grok Pro"
    assert body["period"] == "weekly"
    assert body["usedPercent"] == 88.0
    assert body["resetsAt"] == "2026-10-15T05:25:15+00:00"
    assert body["products"] == [{
        "id": "GrokBuild",
        "label": "Grok Build",
        "percent": 88.0,
        "color": "#1084fe",
    }]
    assert body["cliDataRetentionOptOut"] is True


def test_omitted_percent_is_not_reported_as_zero():
    body = usage.present({
        "config": {
            "currentPeriod": {
                "type": "USAGE_PERIOD_TYPE_WEEKLY",
                "end": "2026-10-15T00:00:00+00:00",
            }
        }
    }, None)
    assert body["ok"] is True
    assert body["usedPercent"] is None
    assert body["products"] == []


def test_fraction_under_one_is_scaled():
    body = usage.present({"config": {"creditUsagePercent": 0.5, "productUsage": []}}, None)
    assert body["usedPercent"] == 50.0


def test_load_usage_without_a_sign_in_does_not_call_the_network(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(usage, "signed_in", lambda: False)

    def explode(*_args, **_kwargs):
        raise AssertionError("network")

    monkeypatch.setattr(usage, "_get_json", explode)
    body = usage.load_usage()
    assert body["signedIn"] is False
    assert body["usedPercent"] is None
    assert body["error"] == "Sign in to see usage."


def test_load_usage_hides_the_bearer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(usage, "signed_in", lambda: True)
    monkeypatch.setattr(usage, "_bearer", lambda: "secret-token-value")

    def fake(token: str, path: str):
        assert token == "secret-token-value"
        if path.startswith("/v1/billing"):
            return _BILLING
        return {"subscriptionTier": "GrokPro", "codingDataRetentionOptOut": True, "email": "hidden@example.com"}

    monkeypatch.setattr(usage, "_get_json", fake)
    body = usage.load_usage()
    text = json.dumps(body)
    assert "secret-token-value" not in text
    assert "hidden@example.com" not in text
    assert body["usedPercent"] == 88.0


def test_load_usage_failure_is_generic(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(usage, "signed_in", lambda: True)
    monkeypatch.setattr(usage, "_bearer", lambda: "secret-token-value")

    def explode(*_args, **_kwargs):
        raise OSError("secret-token-value leaked in the exception")

    monkeypatch.setattr(usage, "_get_json", explode)
    body = usage.load_usage()
    text = json.dumps(body)
    assert body["error"] == "Couldn't load usage."
    assert "secret-token-value" not in text
