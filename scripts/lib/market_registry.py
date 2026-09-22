"""Canonical product-market registry for BDHub-Agent.

The vendored protocol registry describes what TikTok endpoints know.  This file describes which
markets this product actually exposes and the catalogue sources the user enabled for each market.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


MARKET_KEY = re.compile(r"^[a-z]{2}$")
CAPABILITIES = frozenset({"campaignCatalog", "fullManagedCatalog"})
REQUIRED = frozenset({
    "enabled", "label", "shortLabel", "locale", "currency", "timeZone",
    "platformRegion", "templateLanguage", "capabilities",
})


def _root(root=None):
    return Path(root).resolve() if root is not None else Path(__file__).resolve().parents[2]


def validate_registry(value):
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ValueError("market_registry_invalid")
    markets = value.get("markets")
    default = value.get("defaultMarket")
    if not isinstance(markets, dict) or not markets or default not in markets:
        raise ValueError("market_registry_invalid")
    for key, row in markets.items():
        if not isinstance(key, str) or not MARKET_KEY.fullmatch(key) or not isinstance(row, dict):
            raise ValueError("market_registry_invalid")
        if set(row) != REQUIRED or type(row["enabled"]) is not bool:
            raise ValueError("market_registry_invalid")
        for field in ("label", "shortLabel", "locale", "currency", "timeZone",
                      "platformRegion", "templateLanguage"):
            if not isinstance(row[field], str) or not row[field].strip() or len(row[field]) > 80:
                raise ValueError("market_registry_invalid")
        if not row["platformRegion"].isascii() or not row["platformRegion"].isdigit():
            raise ValueError("market_registry_invalid")
        capabilities = row["capabilities"]
        if not isinstance(capabilities, dict) or set(capabilities) != CAPABILITIES or \
           any(type(enabled) is not bool for enabled in capabilities.values()):
            raise ValueError("market_registry_invalid")
        if not capabilities["campaignCatalog"]:
            raise ValueError("campaign_catalog_required")
    return value


def load_registry(root=None):
    path = _root(root) / "config/markets.json"
    return validate_registry(json.loads(path.read_text(encoding="utf-8")))


def enabled_market_keys(root=None):
    try:registry=load_registry(root)
    except FileNotFoundError:return ("it",)
    return tuple(key for key, row in registry["markets"].items() if row["enabled"])


def market(root, key):
    row = load_registry(root)["markets"].get(key)
    if not row or row["enabled"] is not True:
        raise ValueError("market_not_enabled")
    return {"key": key, **row}


def supports(root, key, capability):
    if capability not in CAPABILITIES:
        raise ValueError("market_capability_invalid")
    return market(root, key)["capabilities"][capability]
