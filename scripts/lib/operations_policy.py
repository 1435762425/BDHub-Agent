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
            head = (db.execute(
                """SELECT r.scope FROM global_source_head h JOIN global_source_run r ON r.id=h.run_id
                   WHERE json_extract(r.scope,'$.market')=? AND r.state IN ('completed','accepted_partial')
                     AND r.identity_unchanged=1""", (market,),
            ).fetchone() if market == "it" else None)
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
        try:
            if not head or json.loads(head[0]).get("partitionMode") != "category_l1_v1":
                raise ValueError("it_plain_baseline_unavailable")
        except (TypeError, json.JSONDecodeError):
            raise ValueError("it_plain_baseline_unavailable") from None
        return {"mode": "plain", "reason": "operator_plain_only", "lastCategoryAt": float(last)}
    age = max(0.0, float(now) - float(last))
    threshold = policy["fullManagedCategoryRefreshDays"] * 86400
    if age >= threshold:
        return {"mode": "category", "reason": "monthly_category_refresh", "lastCategoryAt": float(last)}
    return {"mode": "plain", "reason": "weekly_incremental_refresh", "lastCategoryAt": float(last)}


def published_plain_source(root, market, run_id):
    """Return an exact published plain source for the same workflow run, if one exists."""
    path = Path(root) / ("var/global-source.sqlite" if market == "it" else f"var/global-source-{market}.sqlite")
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        source = db.execute("SELECT state,identity_unchanged,scope FROM global_source_run WHERE id=?",
                            (run_id,)).fetchone()
        head = db.execute("""SELECT r.id,r.state,r.identity_unchanged,r.scope FROM global_source_head h
                             JOIN global_source_run r ON r.id=h.run_id
                             WHERE json_extract(r.scope,'$.market')=?""", (market,)).fetchone()
        if not source or not head or source["state"] != "completed" or not source["identity_unchanged"] or \
           head["state"] not in ("completed", "accepted_partial") or not head["identity_unchanged"]:
            return None
        source_scope = json.loads(source["scope"])
        head_scope = json.loads(head["scope"])
        if source_scope.get("market") != market or source_scope.get("partitionMode") == "category_l1_v1":
            return None
        if head["id"] != run_id and (head_scope.get("coverageOverlay") or {}).get("refreshRunId") != run_id:
            return None
        products = db.execute("SELECT count(*) FROM global_source_product WHERE run_id=?", (head["id"],)).fetchone()[0]
        return {"sourceRunId": run_id, "headRunId": head["id"], "products": products}
