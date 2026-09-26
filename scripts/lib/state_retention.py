"""Keep the current version plus the two most recent complete ones of bulky run history.

Older catalog snapshots, full-managed source runs, collector snapshot files, per-run reports,
scheduler stage logs and account identity generations are written to a verified archive outside
the repository and only then removed; large append-only logs are rotated the same way.  Anything
that a current head, an open workflow run or a pending recovery still references stays in place,
and so does anything younger than its safety window.  ``restore`` puts an archive back.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tarfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from lib.second_cycle import digest

KEEP_VERSIONS = 2
REPORT_DAYS = 14
IDENTITY_SAFETY_HOURS = 72
ROTATE_LOG_BYTES = 20 * 1024 * 1024
# A log still held open by a live writer is only rotated past this size, and then marked as possibly
# missing the bytes appended between the last copy and the truncate (H10).
FORCE_ROTATE_LOG_BYTES = 5 * ROTATE_LOG_BYTES


def open_writers(path):
    """PIDs holding ``path`` open, or None when that cannot be determined (treated as busy)."""
    import subprocess
    try:
        result = subprocess.run(["lsof", "-t", "--", str(path)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode not in (0, 1):
        return None
    return sorted({int(line) for line in result.stdout.split() if line.strip().isdigit()})
COMPLETE_SOURCE_STATES = ("completed", "accepted_partial")
FINISHED_SOURCE_STATES = COMPLETE_SOURCE_STATES + ("stopped", "partial", "failed")
CLOSED_WORKFLOW_STATES = ("completed", "failed", "cancelled")
# Top-level var files that code reads or rewrites in place whatever their age.  Collector snapshot
# files (cycle-catalog-*) have their own rule below.
KEEP_VAR_PREFIXES = ("job-", "market-inbox-", "market-send-worker-", "agent-reply-", "catalog-names-progress-",
                     "catalog-selected-pool-cache", "global-selection-status", "global-source-runtime-",
                     "operations-scheduler", "continuous-send", "cycle-inbox", "cycle-catalog-")
KEEP_VAR_NAMES = frozenset({"it-conversations-scan.json", "italy-profile-probe-targets.json",
                            "acc9-link-acc6-readback-20260914-v3.json", "acc9-link-execute-20260914.json"})
CATALOG_FILE = re.compile(r"^cycle-catalog-(?P<group>.+?)-20\d{6}")


class RetentionError(ValueError):
    pass


def _connect(path, *, readonly):
    suffix = "?mode=ro" if readonly else ""
    connection = sqlite3.connect(Path(path).resolve().as_uri() + suffix, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _open_workflows(root):
    """Return open workflow run ids and the text of their stage records (for file references)."""
    path = Path(root) / "var/second-cycle.sqlite"
    if not path.exists():
        return [], ""
    with closing(_connect(path, readonly=True)) as db:
        tables = _tables(db)
        if "workflow_run" not in tables:
            return [], ""
        marks = ",".join("?" * len(CLOSED_WORKFLOW_STATES))
        runs = [row[0] for row in db.execute(f"SELECT run_id FROM workflow_run WHERE state NOT IN ({marks})",
                                             CLOSED_WORKFLOW_STATES)]
        text = []
        if "workflow_stage_run" in tables:
            for row in db.execute(f"""SELECT s.checkpoint_json,s.counts_json FROM workflow_stage_run s
                                       JOIN workflow_run r ON r.run_id=s.run_id WHERE r.state NOT IN ({marks})""",
                                  CLOSED_WORKFLOW_STATES):
                text.extend(value for value in row if value)
    return runs, "\n".join(text)


def _catalog_candidates(root):
    path = Path(root) / "var/second-cycle.sqlite"
    if not path.exists():
        return []
    with closing(_connect(path, readonly=True)) as db:
        if not {"catalog", "catalog_head"} <= _tables(db):
            return []
        heads = {row[0] for row in db.execute("SELECT snapshot_id FROM catalog_head")}
        rows = db.execute("SELECT id,plan_id,source,observed,state,length(payload) AS bytes FROM catalog").fetchall()
    groups = {}
    for row in rows:
        groups.setdefault((row["plan_id"], row["source"]), []).append(row)
    candidates = []
    for group in groups.values():
        ordered = sorted(group, key=lambda row: row["observed"], reverse=True)
        keep = {row["id"] for row in ordered if row["id"] in heads}
        keep.update(row["id"] for row in [row for row in ordered if row["id"] not in heads
                                          and row["state"] == "complete"][:KEEP_VERSIONS])
        floor = min((row["observed"] for row in ordered if row["id"] in keep), default=None)
        for row in ordered:
            # Only rows older than every kept version go: a newer unfinished snapshot may be in use.
            if floor is not None and row["id"] not in keep and row["observed"] < floor:
                candidates.append({"id": row["id"], "planId": row["plan_id"], "source": row["source"],
                                   "observed": row["observed"], "bytes": row["bytes"] or 0})
    return candidates


def _source_databases(root):
    return sorted(path for path in (Path(root) / "var").glob("global-source*.sqlite") if "-before-" not in path.name)


def _source_candidates(root, open_runs):
    """Full-managed source runs beyond head + two recent complete versions, minus protected runs."""
    suffixes = {digest([run_id, "selected"])[:12] for run_id in open_runs}
    plans = []
    for path in _source_databases(root):
        with closing(_connect(path, readonly=True)) as db:
            tables = _tables(db)
            if not {"global_source_run", "global_source_head"} <= tables:
                continue
            runs = db.execute("SELECT id,state,created FROM global_source_run ORDER BY created DESC").fetchall()
            protected = {}
            for (run_id, scope) in db.execute("""SELECT r.id,r.scope FROM global_source_head h
                                                 JOIN global_source_run r ON r.id=h.run_id"""):
                protected[run_id] = "current head"
                overlay = (json.loads(scope or "{}") or {}).get("coverageOverlay") or {}
                for key in ("baselineRunId", "previousHeadRunId", "refreshRunId"):
                    if overlay.get(key):
                        protected.setdefault(overlay[key], f"referenced by head overlay ({key})")
            heads = {run_id for run_id, reason in protected.items() if reason == "current head"}
            # Referenced runs still count towards the two recent versions; they are not extra ones.
            recent = [run["id"] for run in runs if run["id"] not in heads
                      and run["state"] in COMPLETE_SOURCE_STATES][:KEEP_VERSIONS]
            for run_id in recent:
                protected.setdefault(run_id, "recent complete version")
            for run in runs:
                if run["id"] in protected:
                    continue
                if any(run["id"].endswith("-" + suffix) for suffix in suffixes):
                    protected[run["id"]] = "source of an open workflow run"
                elif run["state"] not in FINISHED_SOURCE_STATES:
                    protected[run["id"]] = f"not finished ({run['state']})"
            candidates = [run["id"] for run in runs if run["id"] not in protected]
            child_tables = sorted(table for table in tables
                                  if table not in ("global_source_run", "global_source_head",
                                                   "global_source_screen_run", "global_source_screen",
                                                   "global_source_operator_acceptance")
                                  and "run_id" in {row[1] for row in db.execute(f"PRAGMA table_info('{table}')")})
            screens = {}
            if {"global_source_screen_run", "global_source_screen"} <= tables:
                for run_id in candidates:
                    screens[run_id] = [row[0] for row in db.execute(
                        "SELECT run_id FROM global_source_screen_run WHERE source_run=?", (run_id,))]
            counts = {}
            for run_id in candidates:
                counts[run_id] = sum(db.execute(f"SELECT count(*) FROM {table} WHERE run_id=?", (run_id,)).fetchone()[0]
                                     for table in child_tables)
                counts[run_id] += sum(db.execute("SELECT count(*) FROM global_source_screen WHERE run_id=?",
                                                 (screen,)).fetchone()[0] for screen in screens.get(run_id, []))
            # A run keeps its metadata row after archiving; one with no rows left is already done.
            candidates = [run_id for run_id in candidates if counts[run_id]]
            counts = {run_id: counts[run_id] for run_id in candidates}
            screens = {run_id: screens.get(run_id, []) for run_id in candidates}
        plans.append({"database": path.name, "candidates": candidates, "rows": counts, "screens": screens,
                      "childTables": child_tables, "protected": protected})
    return plans


def _mtime(path):
    return path.stat().st_mtime


def _file_candidates(root, now, workflow_text):
    var = Path(root) / "var"
    cutoff = now - REPORT_DAYS * 86400
    catalog_groups, reports = {}, []
    for path in sorted(var.glob("*.json")):
        if path.name in workflow_text:
            continue
        match = CATALOG_FILE.match(path.name)
        if match:
            catalog_groups.setdefault(match.group("group"), []).append(path)
        elif (not path.name.startswith(KEEP_VAR_PREFIXES) and path.name not in KEEP_VAR_NAMES
              and _mtime(path) < cutoff):
            reports.append(path)
    # The collector file is only the hand-off into the catalog table, which keeps its own versions:
    # keep the newest file per group for a re-sync and archive the rest.
    catalog_files = [path for group in catalog_groups.values()
                     for path in sorted(group, key=_mtime, reverse=True)[1:]]
    stage_dir = var / "cycle-scheduler"
    stage_logs = sorted(path for path in stage_dir.glob("*.log") if _mtime(path) < cutoff) if stage_dir.is_dir() else []
    rotate = sorted(path for path in var.glob("*.log") if path.stat().st_size > ROTATE_LOG_BYTES)
    return {"catalogFiles": catalog_files, "reports": reports, "stageLogs": stage_logs, "rotateLogs": rotate}


def _identity_candidates(root, now):
    base = Path(root) / "var/account-identities"
    path = Path(root) / "var/second-cycle.sqlite"
    if not base.is_dir() or not path.exists():
        return [], {}
    keep = {}
    with closing(_connect(path, readonly=True)) as db:
        if "account_identity_generation" not in _tables(db):
            return [], {}
        for row in db.execute("""SELECT account,browser_ref FROM account_identity_generation
                                 WHERE state='published' ORDER BY published_at DESC"""):
            reference = row["browser_ref"] or ""
            if reference.startswith("project-browser:"):
                ordered = keep.setdefault(row["account"], [])
                candidate = reference.split(":", 1)[1]
                if candidate not in ordered and len(ordered) < 2:
                    ordered.append(candidate)
    cutoff = now - IDENTITY_SAFETY_HOURS * 3600
    candidates = []
    for account_dir in sorted(base.iterdir()):
        generations = account_dir / "generations"
        if not generations.is_dir():
            continue
        current = keep.get(account_dir.name, [])
        for generation in sorted(generations.iterdir()):
            if generation.is_dir() and generation.name not in current and _mtime(generation) < cutoff:
                candidates.append(generation)
    return candidates, keep


def plan(root, *, now=None):
    """Read-only: what would be archived, and what is protected and why."""
    root = Path(root)
    now = time.time() if now is None else now
    open_runs, workflow_text = _open_workflows(root)
    files = _file_candidates(root, now, workflow_text)
    identities, identity_keep = _identity_candidates(root, now)
    relative = lambda paths: [str(path.relative_to(root)) for path in paths]  # noqa: E731
    return {"schemaVersion": "bdhub.state-retention-plan.v1", "keepVersions": KEEP_VERSIONS,
            "reportDays": REPORT_DAYS, "openWorkflowRuns": open_runs,
            "catalogSnapshots": _catalog_candidates(root),
            "sourceRuns": _source_candidates(root, open_runs),
            "catalogFiles": relative(files["catalogFiles"]), "reports": relative(files["reports"]),
            "stageLogs": relative(files["stageLogs"]), "rotateLogs": relative(files["rotateLogs"]),
            "identityGenerations": relative(identities), "identityKept": identity_keep}


def _jsonable(value):
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode()}
    return value


def _restorable(value):
    if isinstance(value, dict) and set(value) == {"__bytes__"}:
        return base64.b64decode(value["__bytes__"])
    return value


def _sha256(path):
    digest_ = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest_.update(chunk)
    return digest_.hexdigest()


def _write_rows(archive_path, database, groups):
    """groups: [(table, sql, params)].  Returns the number of rows written, re-read to verify."""
    written = 0
    with gzip.open(archive_path, "wt", encoding="utf-8") as out, closing(_connect(database, readonly=True)) as db:
        for table, sql, params in groups:
            for row in db.execute(sql, params):
                out.write(json.dumps({"table": table, "row": {key: _jsonable(row[key]) for key in row.keys()}},
                                     ensure_ascii=False) + "\n")
                written += 1
    with gzip.open(archive_path, "rt", encoding="utf-8") as check:
        if sum(1 for _ in check) != written:
            raise RetentionError("state_retention_archive_mismatch")
    return written


def _write_tar(archive_path, root, relative_paths):
    with tarfile.open(archive_path, "w:gz") as tar:
        for relative_path in relative_paths:
            tar.add(Path(root) / relative_path, arcname=relative_path)
    with tarfile.open(archive_path, "r:gz") as tar:
        names = set(tar.getnames())
    if any(path not in names for path in relative_paths):
        raise RetentionError("state_retention_archive_mismatch")


def apply(root, archive_root, *, now=None, vacuum=False):
    """Archive every planned candidate, verify the archive, then remove the originals."""
    root = Path(root)
    now = time.time() if now is None else now
    current = plan(root, now=now)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = Path(archive_root) / stamp
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    manifest = {"schemaVersion": "bdhub.state-retention-archive.v1", "createdAt": now, "root": str(root),
                "plan": current, "archives": []}
    touched = set()

    snapshots = current["catalogSnapshots"]
    if snapshots:
        database = root / "var/second-cycle.sqlite"
        ids = [row["id"] for row in snapshots]
        archive = target / "catalog-snapshots.jsonl.gz"
        marks = ",".join("?" * len(ids))
        rows = _write_rows(archive, database, [("catalog", f"SELECT * FROM catalog WHERE id IN ({marks})", ids)])
        with closing(_connect(database, readonly=False)) as db, db:
            heads = {row[0] for row in db.execute("SELECT snapshot_id FROM catalog_head")}
            if heads & set(ids):
                raise RetentionError("state_retention_head_changed")
            db.execute(f"DELETE FROM catalog WHERE id IN ({marks})", ids)
        touched.add(database)
        manifest["archives"].append({"file": archive.name, "database": str(database.relative_to(root)),
                                     "rows": rows, "sha256": _sha256(archive)})

    for source in current["sourceRuns"]:
        if not source["candidates"]:
            continue
        database = root / "var" / source["database"]
        groups = []
        for run_id in source["candidates"]:
            groups.extend((table, f"SELECT * FROM {table} WHERE run_id=?", (run_id,)) for table in source["childTables"])
            groups.extend(("global_source_screen", "SELECT * FROM global_source_screen WHERE run_id=?", (screen,))
                          for screen in source["screens"].get(run_id, []))
        archive = target / f"source-runs-{database.stem}.jsonl.gz"
        rows = _write_rows(archive, database, groups)
        with closing(_connect(database, readonly=False)) as db, db:
            heads = {row[0] for row in db.execute("SELECT run_id FROM global_source_head")}
            if heads & set(source["candidates"]):
                raise RetentionError("state_retention_head_changed")
            for table, sql, params in groups:
                db.execute(f"DELETE FROM {table} WHERE run_id=?", params)
        touched.add(database)
        manifest["archives"].append({"file": archive.name, "database": str(database.relative_to(root)),
                                     "runs": source["candidates"], "rows": rows, "sha256": _sha256(archive)})

    for key, name in (("catalogFiles", "catalog-files.tar.gz"), ("reports", "reports.tar.gz"),
                      ("stageLogs", "stage-logs.tar.gz"), ("identityGenerations", "identity-generations.tar.gz")):
        paths = current[key]
        if not paths:
            continue
        archive = target / name
        _write_tar(archive, root, paths)
        for relative_path in paths:
            path = root / relative_path
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        manifest["archives"].append({"file": archive.name, "paths": paths, "sha256": _sha256(archive)})

    for relative_path in current["rotateLogs"]:
        path = root / relative_path
        # Writers are resident processes holding O_APPEND descriptors with no reopen protocol: a line
        # appended after the copy reached EOF but before the truncate would be lost. Rotate only logs
        # nobody writes; a live writer's log waits for it to exit unless it outgrows the hard cap.
        writers = open_writers(path)
        if writers != [] and path.stat().st_size <= FORCE_ROTATE_LOG_BYTES:
            manifest.setdefault("skippedLogs", []).append(
                {"log": relative_path, "reason": "writer_active" if writers else "writers_unknown"})
            continue
        archive = target / f"{path.name}.{stamp}.gz"
        with open(path, "rb") as source_file, gzip.open(archive, "wb") as out:
            shutil.copyfileobj(source_file, out, 1 << 20)
            # Catch up with whatever was appended while copying, right before the truncate.
            shutil.copyfileobj(source_file, out, 1 << 20)
            size = source_file.tell()
        with gzip.open(archive, "rb") as check:
            if sum(len(chunk) for chunk in iter(lambda: check.read(1 << 20), b"")) < size:
                raise RetentionError("state_retention_archive_mismatch")
        # Writers append with O_APPEND, so truncating keeps their descriptors valid.
        os.truncate(path, 0)
        manifest["archives"].append({"file": archive.name, "rotatedLog": relative_path, "bytes": size,
                                     "mayMissTail": bool(writers) or writers is None,
                                     "sha256": _sha256(archive)})

    if vacuum:
        for database in sorted(touched):
            with closing(sqlite3.connect(database, timeout=60)) as db:
                db.execute("VACUUM")
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path in target.iterdir():
        path.chmod(0o600)
    return {"archive": str(target), "archives": manifest["archives"], "vacuumed": [str(p.relative_to(root)) for p in sorted(touched)] if vacuum else []}


def restore(root, archive_dir):
    """Put an archive back: re-insert missing rows and extract missing files.  Rotated logs stay archived."""
    root = Path(root)
    archive_dir = Path(archive_dir)
    manifest = json.loads((archive_dir / "manifest.json").read_text(encoding="utf-8"))
    restored = []
    for entry in manifest["archives"]:
        path = archive_dir / entry["file"]
        if _sha256(path) != entry["sha256"]:
            raise RetentionError("state_retention_archive_corrupt")
        if "database" in entry:
            count = 0
            with gzip.open(path, "rt", encoding="utf-8") as lines, \
                    closing(_connect(root / entry["database"], readonly=False)) as db, db:
                for line in lines:
                    item = json.loads(line)
                    row = {key: _restorable(value) for key, value in item["row"].items()}
                    columns = ",".join(row)
                    db.execute(f"INSERT OR IGNORE INTO {item['table']}({columns}) VALUES({','.join('?' * len(row))})",
                               list(row.values()))
                    count += 1
            restored.append({"file": entry["file"], "rows": count})
        elif "paths" in entry:
            with tarfile.open(path, "r:gz") as tar:
                members = [member for member in tar.getmembers() if not (root / member.name).exists()]
                tar.extractall(root, members=members, filter="data")
            restored.append({"file": entry["file"], "members": len(members)})
    return {"restored": restored}
