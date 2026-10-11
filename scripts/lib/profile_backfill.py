"""Preview/import only TikTok Find profiles already bound by the identity ledger."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from lib.creator_core_profile import CORE_FIELDS, find_core_observations, _observed_at
from lib.creator_identity import CreatorIdentityStore, IdentityConflict


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read(path, var):
    resolved = path.resolve()
    if not resolved.is_relative_to(var) or not resolved.is_file() or resolved.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("profile_backfill_input_invalid")
    value = json.loads(resolved.read_text())
    if not isinstance(value, dict):
        raise ValueError("profile_backfill_input_invalid")
    return value


def preview(root, *, markets=None, report_files=None):
    """Read immutable reports and identity observations without opening a write store."""
    root = Path(root).resolve()
    var = (root / "var").resolve()
    path = var / "creator-identities.sqlite"
    if markets is not None and (not isinstance(markets, (set, list, tuple)) or
                               not set(markets) <= {"it", "br", "my", "th", "uk", "de", "fr"}):
        raise ValueError("profile_backfill_market_invalid")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as database:
        database.row_factory = sqlite3.Row
        database.execute("BEGIN")
        canonical = {(r["market"], r["oec_id"]): r["creator_id"] for r in database.execute(
            "SELECT market,oec_id,creator_id FROM creator_identity")}
        proofs = {(r["market"], r["oec_id"], r["evidence_ref"]): dict(r) for r in database.execute(
            "SELECT market,oec_id,creator_id,handle,evidence_ref,observed_at,fingerprint,payload_json "
            "FROM identity_observation WHERE kind='profile' AND outcome='observed' AND evidence_ref LIKE 'market-profile-probe:%:find'")}
        imported = {(r["market"], r["oec_id"], r["evidence_ref"], _observed_at(r["observed_at"])): dict(r)
                    for r in database.execute("SELECT market,oec_id,evidence_ref,observed_at,handle,payload_json FROM identity_observation "
                                              "WHERE kind='profile' AND evidence_ref LIKE 'market-profile-probe:%:find-profile'")}
    if report_files is None:
        files = sorted(var.glob("market-identity-*/output/report.private.json"))
    else:
        if not isinstance(report_files, list) or any(not isinstance(f, str) for f in report_files) or len(set(report_files)) != len(report_files):
            raise ValueError("profile_backfill_input_invalid")
        files = sorted(root / f for f in report_files)
        if any(not file.resolve().is_relative_to(var) or file.name != "report.private.json" for file in files):
            raise ValueError("profile_backfill_input_invalid")
    rows, skipped, scanned, selected_files = [], Counter(), 0, []
    for file in files:
        try:
            report = _read(file, var)
            market = report.get("market")
            if markets is not None and market not in markets:
                continue
            scanned += 1
            selected_files.append(str(file.relative_to(root)))
            targets = _read(file.parent.parent / "targets.private.json", var)
            if targets.get("market", "it") != market:
                raise ValueError("profile_backfill_input_invalid")
            requested = [{"ref": t["ref"], "handle": t["handle"].strip().lstrip("@").lower(),
                          "sourceId": t["externalId"]} for t in targets["targets"]]
            observations = find_core_observations(report, requested, market=market, account=report["account"])
            for observation in observations:
                identity_key = (market, observation["oecId"])
                proof_ref = observation["evidenceRef"].removesuffix(":find-profile") + ":find"
                proof = proofs.get((*identity_key, proof_ref))
                if proof is None or proof["creator_id"] != canonical.get(identity_key) or \
                        proof["handle"] != observation["handle"] or _observed_at(proof["observed_at"]) != observation["observedAt"]:
                    skipped["not_bound_by_original_identity_ledger"] += 1
                    continue
                exact = json.loads(proof["payload_json"]).get("exactFind")
                if exact != {"market": market, "queriedHandle": observation["handle"], "returnedHandle": observation["handle"],
                             "oecId": observation["oecId"], "httpStatus": 200, "code": 0, "verificationRequired": False}:
                    skipped["original_find_proof_incomplete"] += 1
                    continue
                existing = imported.get((*identity_key, observation["evidenceRef"], observation["observedAt"]))
                if existing is not None:
                    if existing["handle"] != observation["handle"] or json.loads(existing["payload_json"]) != observation["payload"]:
                        raise IdentityConflict("historical profile evidence has conflicting facts")
                    skipped["already_imported"] += 1
                rows.append({**observation, "creatorId": proof["creator_id"], "originalEvidenceRef": proof_ref,
                             "originalFingerprint": proof["fingerprint"], "report": str(file.relative_to(root)),
                             "alreadyImported": existing is not None})
        except IdentityConflict:
            raise
        except (ValueError, TypeError, KeyError, OSError):
            skipped["invalid_report"] += 1
    rows.sort(key=lambda r: (r["observedAt"], r["market"], r["oecId"], r["evidenceRef"]))
    stats = {}
    for market in sorted({r["market"] for r in rows}):
        values = [r for r in rows if r["market"] == market]
        stats[market] = {"observations": len(values), "creators": len({r["creatorId"] for r in values}),
                         "coreComplete": sum(r["coreComplete"] for r in values),
                         "unauthorized": sum(any(r["payload"]["fields"][k]["status"] == "unauthorized" for k in CORE_FIELDS) for r in values)}
    digest_rows = [{k: v for k, v in row.items() if k != "alreadyImported"} for row in rows]
    return {"schema": "bdhub.profile-backfill-preview.v1", "previewHash": _hash({"rows": digest_rows, "reportFiles": selected_files}), "reportsScanned": scanned,
            "observations": len(rows), "pendingObservations": sum(not r["alreadyImported"] for r in rows),
            "markets": stats, "skipped": dict(skipped), "rows": rows, "reportFiles": selected_files,
            "portraitSource": "tiktok", "platformRequests": 0, "productionWrites": 0}


def apply(root, preview_hash, *, markets=None, report_files=None, progress=None):
    """Recompute the preview; import only unchanged, already-confirmed stable identities."""
    result = preview(root, markets=markets, report_files=report_files)
    if not isinstance(preview_hash, str) or preview_hash != result["previewHash"]:
        raise ValueError("profile_backfill_preview_changed")
    root = Path(root).resolve()
    imported = 0
    with CreatorIdentityStore(root / "var/creator-identities.sqlite") as identities:
        for row in result["rows"]:
            if row["alreadyImported"]:
                continue
            # Recheck the original immutable fact at consumption time. No handle
            # lead resolution or business offer/relationship projection occurs.
            original = identities._db.execute(
                "SELECT creator_id,fingerprint FROM identity_observation WHERE market=? AND oec_id=? AND evidence_ref=? AND observed_at=?",
                (row["market"], row["oecId"], row["originalEvidenceRef"], row["observedAt"])).fetchone()
            if original is None or original["creator_id"] != row["creatorId"] or original["fingerprint"] != row["originalFingerprint"]:
                raise IdentityConflict("original Find identity proof changed")
            _, inserted = identities.observe_known_profile(row["creatorId"], row["market"], row["oecId"], row["handle"],
                                             row["observedAt"], row["evidenceRef"], row["payload"], return_inserted=True)
            imported += inserted
            if progress is not None and imported % 500 == 0:
                progress({"imported": imported, "total": len(result["rows"])})
    return {k: v for k, v in result.items() if k not in {"rows", "reportFiles"}} | {"imported": imported, "productionWrites": imported}
