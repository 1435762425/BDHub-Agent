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
RUNTIME_STATES = frozenset({"ready", "paused", "planned"})
REQUIRED = frozenset({
    "enabled", "runtimeState", "contentReady", "label", "shortLabel", "locale",
    "currency", "timeZone", "platformRegion", "templateLanguage", "accounts", "capabilities",
})


def _root(root=None):
    return Path(root).resolve() if root is not None else Path(__file__).resolve().parents[2]


def validate_registry(value):
    if not isinstance(value, dict) or value.get("schemaVersion") != 2:
        raise ValueError("market_registry_invalid")
    markets = value.get("markets")
    default = value.get("defaultMarket")
    if not isinstance(markets, dict) or not markets or default not in markets:
        raise ValueError("market_registry_invalid")
    for key, row in markets.items():
        if not isinstance(key, str) or not MARKET_KEY.fullmatch(key) or not isinstance(row, dict):
            raise ValueError("market_registry_invalid")
        if set(row) != REQUIRED or type(row["enabled"]) is not bool or \
           row["runtimeState"] not in RUNTIME_STATES or type(row["contentReady"]) is not bool:
            raise ValueError("market_registry_invalid")
        for field in ("label", "shortLabel", "platformRegion"):
            if not isinstance(row[field], str) or not row[field].strip() or len(row[field]) > 80:
                raise ValueError("market_registry_invalid")
        for field in ("locale", "currency", "timeZone", "templateLanguage"):
            if row[field] is not None and (not isinstance(row[field], str) or not row[field].strip() or len(row[field]) > 80):
                raise ValueError("market_registry_invalid")
        if not row["platformRegion"].isascii() or not row["platformRegion"].isdigit():
            raise ValueError("market_registry_invalid")
        capabilities = row["capabilities"]
        if not isinstance(capabilities, dict) or set(capabilities) != CAPABILITIES or \
           capabilities["campaignCatalog"] is not True or \
           not (type(capabilities["fullManagedCatalog"]) is bool or capabilities["fullManagedCatalog"] is None):
            raise ValueError("market_registry_invalid")
        accounts=row["accounts"]
        if not isinstance(accounts,dict) or set(accounts)!={"communications","supply"}:
            raise ValueError("market_registry_invalid")
        values=tuple(accounts.values())
        if row["runtimeState"]=="planned":
            if values!=(None,None):raise ValueError("planned_market_accounts_must_be_empty")
        elif any(not isinstance(account,str) or not re.fullmatch(r"acc[1-9][0-9]*",account) for account in values) or len(set(values))!=2:
            raise ValueError("operational_market_accounts_invalid")
        if row["runtimeState"]!="planned" and (capabilities["fullManagedCatalog"] is None or not row["contentReady"]):
            raise ValueError("operational_market_contract_incomplete")
        if row["contentReady"] and (row["locale"] is None or row["templateLanguage"] is None):
            raise ValueError("market_content_metadata_missing")
    return value


def load_registry(root=None):
    path = _root(root) / "config/markets.json"
    return validate_registry(json.loads(path.read_text(encoding="utf-8")))


def enabled_market_keys(root=None):
    registry=load_registry(root)
    return tuple(key for key, row in registry["markets"].items() if row["enabled"])


def operational_market_keys(root=None):
    registry=load_registry(root)
    return tuple(key for key,row in registry["markets"].items()
                 if row["enabled"] and row["runtimeState"]!="planned")


def market(root, key):
    row = load_registry(root)["markets"].get(key)
    if not row or row["enabled"] is not True:
        raise ValueError("market_not_enabled")
    return {"key": key, **row}


def supports(root, key, capability):
    if capability not in CAPABILITIES:
        raise ValueError("market_capability_invalid")
    return market(root, key)["capabilities"][capability] is True


def capability_state(root,key,capability):
    if capability not in CAPABILITIES:raise ValueError("market_capability_invalid")
    value=market(root,key)["capabilities"][capability]
    return "supported" if value is True else "unsupported" if value is False else "unavailable"


def require_operational(root,key):
    row=market(root,key)
    if row["runtimeState"]=="planned" or any(not row["accounts"][role] for role in ("communications","supply")):
        raise ValueError("market_runtime_unavailable")
    return row
