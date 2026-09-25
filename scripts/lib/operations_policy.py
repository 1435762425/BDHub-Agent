"""Validated cross-market operating cadence and bounded concurrency policy."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from lib.second_cycle import digest

BEIJING = ZoneInfo("Asia/Shanghai")


DEFAULTS = {
    "schemaVersion": 1,
    "campaignRefreshDays": 2,
    "fullManagedCategoryRefreshDays": 15,
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
    """Discovery every fifteen days; weekly material maintenance reuses admitted candidates."""
    root=Path(root);policy=policy or load_policy(root)
    path=root/('var/global-source.sqlite' if market=='it' else f'var/global-source-{market}.sqlite')
    if not path.exists():
        return {'mode':'category','reason':'market_onboarding','lastCategoryAt':None,'nextDiscoveryAt':now}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute("""SELECT max(CASE WHEN a.accepted_at IS NOT NULL THEN a.accepted_at ELSE r.updated END)
              FROM global_source_run r LEFT JOIN global_source_operator_acceptance a ON a.run_id=r.id
              WHERE json_extract(r.scope,'$.market')=? AND json_extract(r.scope,'$.partitionMode')='category_l1_v1'
              AND json_extract(r.scope,'$.coverageOverlay') IS NULL
              AND r.state IN ('completed','accepted_partial') AND r.identity_unchanged=1""",(market,)).fetchone()
    except sqlite3.Error as error:
        raise ValueError('category_history_unavailable') from error
    last=row[0] if row else None
    if last is None:
        return {'mode':'category','reason':'market_onboarding','lastCategoryAt':None,'nextDiscoveryAt':now}
    due=float(last)+policy['fullManagedCategoryRefreshDays']*86400
    return {'mode':'category' if now>=due else 'reuse',
            'reason':'periodic_category_discovery' if now>=due else 'discovery_not_due',
            'lastCategoryAt':float(last),'nextDiscoveryAt':due}


def current_published_source(root,market):
    path=Path(root)/('var/global-source.sqlite' if market=='it' else f'var/global-source-{market}.sqlite')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        rows=db.execute("""SELECT r.id,(SELECT count(*) FROM global_source_product p WHERE p.run_id=r.id)
          FROM global_source_head h JOIN global_source_run r ON r.id=h.run_id
          WHERE json_extract(r.scope,'$.market')=? AND r.state IN ('completed','accepted_partial')
          AND r.identity_unchanged=1""",(market,)).fetchall()
    if len(rows)!=1:raise ValueError('source_scope_ambiguous')
    return {'headRunId':rows[0][0],'products':rows[0][1]}


def selected_source_run_id(market, run_id, started_at):
    """The one formula for the selected-source run id: Beijing date plus a digest of the workflow run."""
    day = datetime.fromtimestamp(started_at, BEIJING).strftime("%Y%m%d")
    return f"{market}-global-{day}-" + digest([run_id, "selected"])[:12]


def published_plain_source(root, market, run_id):
    """Return the exact published source for this workflow; the legacy function name is retained."""
    path = Path(root) / ("var/global-source.sqlite" if market == "it" else f"var/global-source-{market}.sqlite")
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        source = db.execute("SELECT state,identity_unchanged,scope FROM global_source_run WHERE id=?",
                            (run_id,)).fetchone()
        heads = db.execute("""SELECT r.id,r.state,r.identity_unchanged,r.scope FROM global_source_head h
                              JOIN global_source_run r ON r.id=h.run_id
                              WHERE json_extract(r.scope,'$.market')=?""", (market,)).fetchall()
        if not source or source["state"] != "completed" or not source["identity_unchanged"]:
            return None
        source_scope = json.loads(source["scope"])
        if source_scope.get("market") != market:
            return None
        matching = []
        for head in heads:
            try:
                head_scope = json.loads(head["scope"])
            except (TypeError, ValueError):
                continue
            if head_scope.get("market") != market:
                continue
            if head["id"] != run_id and (head_scope.get("coverageOverlay") or {}).get("refreshRunId") != run_id:
                continue
            matching.append(head)
        if len(matching) != 1:
            return None
        head = matching[0]
        if head["state"] not in ("completed", "accepted_partial") or not head["identity_unchanged"]:
            return None
        products = db.execute("SELECT count(*) FROM global_source_product WHERE run_id=?", (head["id"],)).fetchone()[0]
        return {"sourceRunId": run_id, "headRunId": head["id"], "products": products}
