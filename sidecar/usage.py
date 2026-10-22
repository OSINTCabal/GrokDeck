"""Weekly usage for the signed-in Grok CLI account.

The bearer stays in this process. Responses keep the pool percent, the product
split, the reset time, and the plan name. Identity fields are dropped.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from sidecar.cli_session import auth_file, signed_in

_HOST = "https://cli-chat-proxy.grok.com"
_LABELS = {
    "GrokChat": "Chat",
    "GrokImagine": "Imagine",
    "GrokVoice": "Voice",
    "Voice": "Voice",
    "GrokBuild": "Grok Build",
    "Api": "API",
    "GrokApi": "API",
}
_ORDER = ["Voice", "Imagine", "Chat", "Grok Build", "API"]
_COLORS = {
    "Voice": "#1084fe",
    "Imagine": "#3d6fbf",
    "Chat": "#1c3f73",
    "Grok Build": "#1084fe",
    "API": "#7eb6ff",
}
_TIERS = {
    "GrokPro": "Grok Pro",
    "SuperGrok": "SuperGrok",
    "SuperGrokPlus": "SuperGrok Plus",
    "SuperGrokHeavy": "SuperGrok Heavy",
    "XPremium": "Premium",
    "XPremiumPlus": "Premium+",
    "PremiumPlus": "Premium+",
    "Premium": "Premium",
    "Free": "Free",
}


def _empty(*, signed_in_now: bool, error: str | None) -> dict[str, Any]:
    return {
        "ok": False,
        "signedIn": signed_in_now,
        "tier": None,
        "period": None,
        "usedPercent": None,
        "resetsAt": None,
        "products": [],
        "onDemandCap": None,
        "onDemandUsed": None,
        "prepaid": None,
        "cliDataRetentionOptOut": None,
        "error": error,
    }


def _bearer() -> str | None:
    path = auth_file()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    for name, node in data.items():
        if not isinstance(name, str) or not name.startswith("https://auth.x.ai::"):
            continue
        if not isinstance(node, dict):
            continue
        token = node.get("key")
        if isinstance(token, str) and token:
            return token
    return None


def _get_json(token: str, path: str) -> dict[str, Any] | None:
    with httpx.Client(timeout=12.0) as client:
        response = client.get(
            _HOST + path,
            headers={
                "Authorization": "Bearer " + token,
                "X-XAI-Token-Auth": "xai-grok-cli",
                "Accept": "application/json",
                "User-Agent": "grok-cli",
            },
        )
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _percent(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number < 0 or number > 100:
        return None
    if 0 < number < 1:
        number *= 100
    return round(number, 1)


def _money(node: Any) -> float | None:
    if isinstance(node, dict):
        node = node.get("val")
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None
    return float(node)


def _tier(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 32 or "@" in value:
        return None
    if value in _TIERS:
        return _TIERS[value]
    if re.fullmatch(r"[A-Za-z0-9+ ]+", value):
        return re.sub(r"(?<!^)(?=[A-Z])", " ", value)
    return None


def _when(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40 or "@" in value:
        return None
    if not re.match(r"^\d{4}-\d{2}-\d{2}T", value):
        return None
    return value


def _label(product: str) -> str | None:
    if product in _LABELS:
        return _LABELS[product]
    if not product or len(product) > 32 or not product.replace("_", "").isalnum():
        return None
    return re.sub(r"(?<!^)(?=[A-Z])", " ", product).replace("_", " ")


def present(billing: dict[str, Any] | None, user: dict[str, Any] | None) -> dict[str, Any]:
    config = billing.get("config") if isinstance(billing, dict) else None
    if not isinstance(config, dict):
        return _empty(signed_in_now=True, error="Couldn't load usage.")
    period_node = config.get("currentPeriod") if isinstance(config.get("currentPeriod"), dict) else {}
    period_type = str(period_node.get("type") or "")
    if "WEEKLY" in period_type:
        period = "weekly"
    elif "MONTHLY" in period_type:
        period = "monthly"
    else:
        period = None
    products: list[dict[str, Any]] = []
    raw_products = config.get("productUsage")
    if isinstance(raw_products, list):
        for item in raw_products:
            if not isinstance(item, dict):
                continue
            name = item.get("product")
            if not isinstance(name, str):
                continue
            label = _label(name)
            percent = _percent(item.get("usagePercent"))
            if label is None or percent is None:
                continue
            products.append({
                "id": name if len(name) <= 32 else label,
                "label": label,
                "percent": percent,
                "color": _COLORS.get(label, "#5c6b86"),
            })
    products.sort(key=lambda row: (_ORDER.index(row["label"]) if row["label"] in _ORDER else len(_ORDER), row["label"]))
    opt_out = user.get("codingDataRetentionOptOut") if isinstance(user, dict) else None
    tier = _tier(user.get("subscriptionTier")) if isinstance(user, dict) else None
    return {
        "ok": True,
        "signedIn": True,
        "tier": tier,
        "period": period,
        "usedPercent": _percent(config.get("creditUsagePercent")) if "creditUsagePercent" in config else None,
        "resetsAt": _when(period_node.get("end")) or _when(config.get("billingPeriodEnd")),
        "products": products,
        "onDemandCap": _money(config.get("onDemandCap")),
        "onDemandUsed": _money(config.get("onDemandUsed")),
        "prepaid": _money(config.get("prepaidBalance")),
        "cliDataRetentionOptOut": opt_out if isinstance(opt_out, bool) else None,
        "error": None,
    }


def load_usage() -> dict[str, Any]:
    if not signed_in():
        return _empty(signed_in_now=False, error="Sign in to see usage.")
    token = _bearer()
    if not token:
        return _empty(signed_in_now=False, error="Sign in to see usage.")
    try:
        billing = _get_json(token, "/v1/billing?format=credits")
    except (httpx.HTTPError, OSError, ValueError):
        return _empty(signed_in_now=True, error="Couldn't load usage.")
    user = None
    try:
        user = _get_json(token, "/v1/user?include=subscription")
    except (httpx.HTTPError, OSError, ValueError):
        user = None
    return present(billing, user)
