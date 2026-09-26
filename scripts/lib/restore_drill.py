"""Offline, isolated restore drill for one state backup (§9.21 / G17).

A backup is a sequence of single-database snapshots (``crossDatabaseAtomic=false``): each file is
consistent, but a later snapshot may reference rows a parent database gained after its own snapshot.
The drill restores the backup into a fresh private directory, opens every copy read-only and checks:

* the backup itself (manifest, checksums, integrity) and the restore path;
* declared cross-database references -- a missing parent row whose child was written after the parent
  database's snapshot started is a *future reference* (copy skew) and blocks; an older one is a
  pre-existing orphan and is reported only;
* schema versions against this code (newer than the code blocks; older needs the normal migration);
* identity dependencies of unresolved send intents (credentials are never in a backup, so the matching
  identity files are listed as an external dependency; a missing published generation blocks).

Nothing is started, no network is used, restored copies are never written, and the report states
``platformWrites: 0``.  A drill never repairs, deletes or re-labels anything: a blocked report is the result.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from lib.state_backup import _load_manifest, restore_backup, verify_backup

DRILL_SCHEMA = "bdhub.state-restore-drill.v1"
MARKET_SUFFIX = {"it": "", "br": "-br", "my": "-my", "uk": "-uk"}


def _epoch(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _references():
    """(id, child db, child SQL -> (key, at), parent dbs, parent SQL -> key); ``at`` may be NULL."""
    refs = [
        ("relationship_creator_identity", "second-cycle.sqlite",
         "SELECT creator_id, NULL FROM relationship", ("creator-identities.sqlite",),
         "SELECT creator_id FROM creator_identity"),
        ("delivery_creator_identity", "second-cycle.sqlite",
         "SELECT creator_id, created FROM cycle_delivery", ("creator-identities.sqlite",),
         "SELECT creator_id FROM creator_identity"),
    ]
    for market, suffix in MARKET_SUFFIX.items():
        # Supply preparation reads either a Campaign screen or a full-managed source run.
        refs.append((f"catalog_prepare_source_run:{market}", "catalog-links.sqlite",
                     f"SELECT source_run, created FROM catalog_prepare_run WHERE market='{market}'",
                     (f"campaign-screen{suffix}.sqlite", f"global-source{suffix}.sqlite"),
                     {f"campaign-screen{suffix}.sqlite": "SELECT run_id FROM campaign_screen_run UNION SELECT run_id FROM campaign_pool_run",
                      f"global-source{suffix}.sqlite": "SELECT id FROM global_source_run UNION SELECT run_id FROM global_source_screen_run"}))
        refs.append((f"intake_source_run:{market}", f"global-selection{suffix}.sqlite",
                     "SELECT source_run, created FROM intake_run", (f"global-source{suffix}.sqlite",),
                     "SELECT id FROM global_source_run"))
    return refs


def _open(path):
    return closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True))


def _table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def _check_reference(ref, restored, manifest_entries):
    ref_id, child, child_sql, parents, parent_sql = ref
    if child not in manifest_entries:
        return None  # The child ledger is not part of this backup group: nothing to check.
    missing_dbs = [name for name in parents if name not in manifest_entries]
    present = [name for name in parents if name in manifest_entries]
    if not present:
        return {"id": ref_id, "child": child, "parents": list(parents), "state": "blocked",
                "reason": "dependency_database_missing", "missingDatabases": missing_dbs}
    keys = set()
    for name in present:
        sql = parent_sql[name] if isinstance(parent_sql, dict) else parent_sql
        with _open(restored / name) as db:
            try:
                keys.update(str(row[0]) for row in db.execute(sql))
            except sqlite3.OperationalError:
                continue  # A parent database that has not created this table yet contributes no keys.
    # A row is a copy-skew candidate only if written after some parent database's snapshot began.
    parent_started = min(manifest_entries[name]["snapshotStartedAt"] for name in present)
    with _open(restored / child) as db:
        try:
            rows = list(db.execute(child_sql))
        except sqlite3.OperationalError:
            rows = []
    future, historical = [], 0
    for key, at in rows:
        if key is None or str(key) in keys:
            continue
        stamp = _epoch(at)
        if stamp is not None and stamp < parent_started:
            historical += 1  # Already missing when the parent was copied: not caused by this backup.
        else:
            future.append(str(key))
    state = "blocked" if future else "ok"
    result = {"id": ref_id, "child": child, "parents": list(parents), "state": state, "rows": len(rows),
              "futureOrUndatedMissing": len(future), "historicalOrphans": historical,
              "sample": future[:5]}
    if missing_dbs:
        result["partialParents"] = missing_dbs
    return result


def _schema(restored, manifest_entries):
    from lib.schema_migrations import DATABASES
    rows = []
    for _key, (filename, migrations) in DATABASES.items():
        if filename not in manifest_entries:
            continue
        expected = max(migration.version for migration in migrations)
        with _open(restored / filename) as db:
            actual = db.execute("SELECT max(version) FROM agent_schema_migration").fetchone()[0] \
                if _table_exists(db, "agent_schema_migration") else 0
        state = "blocked" if (actual or 0) > expected else ("migration_required" if (actual or 0) < expected else "ok")
        rows.append({"database": filename, "backupVersion": actual or 0, "codeVersion": expected, "state": state})
    return rows


def _identity_dependencies(restored, manifest_entries, accounts):
    """Unresolved send intents need their market's sending identity; files are never in a backup."""
    if "second-cycle.sqlite" not in manifest_entries:
        return []
    out = []
    with _open(restored / "second-cycle.sqlite") as db:
        markets = {}
        queries = (
            ("service_reply", "SELECT p.market,count(*) FROM service_reply r JOIN plan p ON p.id=r.plan_id "
             "WHERE r.state IN ('inflight','accepted','unknown','isolated') GROUP BY p.market"),
            ("cycle_delivery_part", "SELECT p.market,count(*) FROM cycle_delivery_part x JOIN cycle_delivery d "
             "ON d.id=x.delivery_id JOIN plan p ON p.id=d.plan_id WHERE x.state IN ('inflight','accepted','unknown') "
             "GROUP BY p.market"),
            ("cycle_conversation_intent", "SELECT p.market,count(*) FROM cycle_conversation_intent c JOIN cycle_delivery d "
             "ON d.id=c.delivery_id JOIN plan p ON p.id=d.plan_id WHERE c.state IN ('inflight','accepted','unknown') "
             "GROUP BY p.market"),
        )
        for table, sql in queries:
            if not _table_exists(db, table):
                continue
            for market, count in db.execute(sql):
                markets.setdefault(market, {})[table] = count
        has_generations = _table_exists(db, "account_identity_generation")
        for market, counts in sorted(markets.items()):
            account = accounts.get(market)
            published = None
            if account and has_generations:
                published = db.execute(
                    "SELECT generation_id,published_at FROM account_identity_generation WHERE market=? AND account=? "
                    "AND state='published' ORDER BY published_at DESC LIMIT 1", (market, account)).fetchone()
            out.append({"market": market, "account": account, "unresolved": counts,
                        "publishedGeneration": published[0] if published else None,
                        "identityFiles": "external_required",
                        "state": "ok" if published else "blocked"})
    return out


def _intent_dependencies(restored, manifest_entries):
    """Per unresolved intent: is the identity it was frozen to still resolvable from this backup (H09)?

    An AI/manual reply freezes its sender account and IM identity: it needs a published generation of
    that exact account (not merely of today's configured account). A delivery component records no
    frozen sender, so its original identity cannot be proven from the ledger and stays ``unverified``."""
    if "second-cycle.sqlite" not in manifest_entries:
        return []
    rows = []
    with _open(restored / "second-cycle.sqlite") as db:
        generations = set()
        if _table_exists(db, "account_identity_generation"):
            generations = {(market, account) for market, account in db.execute(
                "SELECT market,account FROM account_identity_generation WHERE state='published'")}
        if _table_exists(db, "service_reply"):
            columns = {row[1] for row in db.execute("PRAGMA table_info(service_reply)")}
            frozen = "sender_account" in columns and "sender_identity" in columns
            for row in db.execute("SELECT r.id,r.state,p.market" + (",r.sender_account,r.sender_identity" if frozen else ",NULL,NULL")
                                  + " FROM service_reply r JOIN plan p ON p.id=r.plan_id "
                                    "WHERE r.state IN ('inflight','accepted','unknown','isolated') ORDER BY r.id"):
                intent, state, market, account, identity = row
                status = ("identity_not_frozen" if not account or not identity else
                          "account_generation_present" if (market, account) in generations else "account_generation_missing")
                rows.append({"kind": "service_reply", "intent": intent, "state": state, "market": market,
                             "frozenAccount": account, "frozenIdentity": bool(identity), "status": status})
        for table, sql in (
                ("cycle_delivery_part", "SELECT x.delivery_id||':'||x.kind,x.state,p.market FROM cycle_delivery_part x JOIN cycle_delivery d "
                 "ON d.id=x.delivery_id JOIN plan p ON p.id=d.plan_id WHERE x.state IN ('inflight','accepted','unknown') ORDER BY 1"),
                ("cycle_conversation_intent", "SELECT c.delivery_id,c.state,p.market FROM cycle_conversation_intent c JOIN cycle_delivery d "
                 "ON d.id=c.delivery_id JOIN plan p ON p.id=d.plan_id WHERE c.state IN ('inflight','accepted','unknown') ORDER BY 1")):
            if not _table_exists(db, table):
                continue
            for intent, state, market in db.execute(sql):
                rows.append({"kind": table, "intent": intent, "state": state, "market": market,
                             "frozenAccount": None, "frozenIdentity": False, "status": "identity_not_frozen"})
    return rows


def run_drill(backup, *, databases=None, accounts=None, work_dir=None, keep=False, clock=time.time):
    """Restore ``backup`` (optionally a subset of its databases) into an isolated directory and check it."""
    started = float(clock())
    report = {"schema": DRILL_SCHEMA, "backup": str(Path(backup)), "startedAt": started,
              "isolation": {"network": "not_used", "processesStarted": 0, "restoredCopiesOpened": "read_only"},
              "platformWrites": 0, "blockers": []}
    try:
        verification = verify_backup(backup)
        manifest = _load_manifest(Path(backup).resolve())
    except (OSError, ValueError, sqlite3.Error) as error:
        report.update(state="blocked", blockers=[f"backup_invalid:{error}"], finishedAt=float(clock()))
        return report
    entries = {row["name"]: row for row in manifest["databases"]}
    if databases:
        unknown = sorted(set(databases) - set(entries))
        if unknown:
            report.update(state="blocked", blockers=[f"database_not_in_backup:{name}" for name in unknown],
                          finishedAt=float(clock()))
            return report
        entries = {name: entries[name] for name in databases}
    starts = [row["snapshotStartedAt"] for row in entries.values()]
    ends = [row["snapshotFinishedAt"] for row in entries.values()]
    report["group"] = {"databases": sorted(entries), "partial": bool(databases),
                       "snapshotWindowSeconds": round(max(ends) - min(starts), 3),
                       "crossDatabaseAtomic": False, "sourceCommit": verification["gitCommit"],
                       "createdAt": verification["createdAt"]}
    parent = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="bdhub-restore-drill-"))
    target = parent / "var"
    try:
        restore_backup(backup, target, confirmed=True, clock=clock)
        restored = target
        references = [row for row in (_check_reference(ref, restored, entries) for ref in _references()) if row]
        schema = _schema(restored, entries)
        identities = _identity_dependencies(restored, entries, accounts or {})
        intents = _intent_dependencies(restored, entries)
    except (OSError, ValueError, sqlite3.Error) as error:
        report.update(state="blocked", blockers=[f"restore_failed:{error}"], finishedAt=float(clock()))
        return report
    finally:
        if not keep and not work_dir:
            shutil.rmtree(parent, ignore_errors=True)
    report.update(references=references, schema=schema, identityDependencies=identities,
                  intentDependencies=intents[:200], intentDependencyCount=len(intents))
    report["blockers"] = (
        [f"reference:{row['id']}:{row.get('reason') or 'future_or_undated_missing'}" for row in references
         if row["state"] == "blocked"]
        + [f"schema_newer_than_code:{row['database']}" for row in schema if row["state"] == "blocked"]
        + [f"identity_generation_missing:{row['market']}" for row in identities if row["state"] == "blocked"]
        + sorted({f"frozen_identity_missing:{row['market']}:{row['frozenAccount']}" for row in intents
                  if row["status"] == "account_generation_missing"}))
    report["warnings"] = (
        [f"historical_orphans:{row['id']}:{row['historicalOrphans']}" for row in references if row.get("historicalOrphans")]
        + [f"migration_required:{row['database']}" for row in schema if row["state"] == "migration_required"])
    unverified = sum(row["status"] == "identity_not_frozen" for row in intents)
    if unverified:
        report["warnings"].append(f"intent_identity_unverified:{unverified}")
    report["state"] = "blocked" if report["blockers"] else "restorable"
    # What each layer proves (H09). "restorable" means the data restores and is consistent; resuming
    # business also needs the external identity files and every original intent's frozen identity.
    report["layers"] = {
        "snapshotValid": True,
        "schemaCompatible": not any(row["state"] == "blocked" for row in schema),
        "declaredReferencesChecked": not any(row["state"] == "blocked" for row in references),
        "originalIntentDependenciesChecked": "complete" if not intents or not unverified and not any(
            row["status"] == "account_generation_missing" for row in intents) else "partial",
        "externalIdentityAvailability": "external_required",
        "businessResumeProven": False}
    report["restoredTo"] = str(target) if keep or work_dir else None
    report["finishedAt"] = float(clock())
    return report


def write_report(root, report):
    """Keep drill evidence beside the backups, never inside a backup directory (it must stay exact)."""
    directory = Path(root) / "var/backups/drills"
    directory.mkdir(parents=True, exist_ok=True)
    name = Path(report["backup"]).name + "-" + datetime.fromtimestamp(report["startedAt"]).strftime("%Y%m%dT%H%M%S")
    path = directory / (name + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path
