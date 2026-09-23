"""Validated cross-market operating cadence and bounded concurrency policy."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path


DEFAULTS = {
    "schemaVersion": 1,
    "campaignRefreshDays": 2,
    "fullManagedCategoryRefreshDays": 30,
    "kalodataMaxParallelMarkets": 2,
    "sendTemplateApprovalMinimum": 10,
}


def load_policy(root):
    try:
        value = json.loads((Path(root) / "config/operations-policy.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raise ValueError("operations_policy_invalid") from None
    if not isinstance(value, dict) or set(value) != set(DEFAULTS) or value.get("schemaVersion") != 1:
        raise ValueError("operations_policy_invalid")
    bounds = {
        "campaignRefreshDays": (1, 14),
        "fullManagedCategoryRefreshDays": (7, 90),
        "kalodataMaxParallelMarkets": (1, 2),
        "sendTemplateApprovalMinimum": (10, 50),
    }
    for key, (low, high) in bounds.items():
        if type(value.get(key)) is not int or not low <= value[key] <= high:
            raise ValueError("operations_policy_invalid")
    return value


def full_catalog_collection_mode(root, market, now, *, policy=None):
    """Choose the bounded weekly query; only markets still using discovery run category reads."""
    root = Path(root)
    policy = policy or load_policy(root)
    path = root / ("var/global-source.sqlite" if market == "it" else f"var/global-source-{market}.sqlite")
    if not path.exists():
        if market == "it":
            raise ValueError("it_plain_baseline_unavailable")
        return {"mode": "category", "reason": "market_onboarding", "lastCategoryAt": None}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute(
                """SELECT max(CASE WHEN a.accepted_at IS NOT NULL THEN a.accepted_at ELSE r.updated END)
                   FROM global_source_run r
                   LEFT JOIN global_source_operator_acceptance a ON a.run_id=r.id
                   WHERE json_extract(r.scope,'$.market')=?
                     AND json_extract(r.scope,'$.partitionMode')='category_l1_v1'
                     AND json_extract(r.scope,'$.coverageOverlay') IS NULL
                     AND r.state IN ('completed','accepted_partial') AND r.identity_unchanged=1""",
                (market,),
            ).fetchone()
    except sqlite3.Error as error:
        # A transient lock or incompatible history must never silently expand a weekly read into
        # the expensive category-wide crawl.  Preserve the published head and surface the blocker.
        raise ValueError("category_history_unavailable") from error
    last = row[0] if row else None
    if last is None:
        if market == "it":
            raise ValueError("it_plain_baseline_unavailable")
        return {"mode": "category", "reason": "market_onboarding", "lastCategoryAt": None}
    if market == "it":
        return {"mode": "plain", "reason": "operator_plain_only", "lastCategoryAt": float(last)}
    age = max(0.0, float(now) - float(last))
    threshold = policy["fullManagedCategoryRefreshDays"] * 86400
    if age >= threshold:
        return {"mode": "category", "reason": "monthly_category_refresh", "lastCategoryAt": float(last)}
    return {"mode": "plain", "reason": "weekly_incremental_refresh", "lastCategoryAt": float(last)}
