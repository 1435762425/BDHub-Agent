"""Read-only facts for the fixed Italy second-source cohort.

An explicit local exact-Find resolution establishes a current recipient. It
does not prove that the historical Kalodata sales belong to that recipient.
No handle lookup, network transport or business mutation is available here.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SHA256 = "6b2935557c95fd7fb2f2d8d13c9e3a6efb2baf2135c11c1c6c27785831ba8816"
ITALIAN_PRODUCT_NAMES = {
    "1729779362302171335": "compresse per la bocca Freegrin",
    "1729480019490150432": "cuscino cervicale",
    "1729502070782139035": "leggings da yoga",
    "1729584712875612996": "body modellante da donna",
    "1729480472768911992": "multimetro digitale NJTY T3",
}


class SecondOutreachSourceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _text(value, maximum=300):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise SecondOutreachSourceError("invalid_source")
    return value


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,64}", value):
        raise SecondOutreachSourceError("invalid_identity")
    return value


def _integer(value, *, positive=False):
    if type(value) is not int or value < (1 if positive else 0) or value > 9_007_199_254_740_991:
        raise SecondOutreachSourceError("invalid_source")
    return value


def _handle(value):
    value = _text(value, 101).removeprefix("@").lower()
    if not re.fullmatch(r"[a-z0-9._]{1,100}", value):
        raise SecondOutreachSourceError("invalid_identity")
    return value


def _timestamp(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError()
        return timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise SecondOutreachSourceError("invalid_resolution") from None


def _fact(value, *, window=False):
    if not isinstance(value, dict):
        raise SecondOutreachSourceError("missing_source")
    result = {"ref": _text(value.get("ref")), "observedAt": _integer(value.get("observedAt")),
              "windowStart": value.get("windowStart"), "windowEnd": value.get("windowEnd")}
    for key in ("windowStart", "windowEnd"):
        if result[key] is not None:
            result[key] = _integer(result[key])
    if window and (result["windowStart"] is None or result["windowEnd"] is None):
        raise SecondOutreachSourceError("missing_source_window")
    if result["windowStart"] is not None and result["windowEnd"] is not None and result["windowStart"] > result["windowEnd"]:
        raise SecondOutreachSourceError("invalid_source_window")
    basis = value.get("windowBasis")
    if basis not in (None, "calendar_date_unknown_timezone"):
        raise SecondOutreachSourceError("invalid_source_window")
    result["windowBasis"] = basis
    return result


def _recipient(row):
    """Validate the explicit source resolution, never compare source/current handles."""
    try:
        if row["external_source"] != "probe_source_reference" or row["market"] != "it" or row["identity_market"] != "it":
            raise SecondOutreachSourceError("invalid_resolution_namespace")
        proof = row["exact_find"]
        if not isinstance(proof, dict) or proof.get("market") != "it" or type(proof.get("httpStatus")) is not int or proof["httpStatus"] != 200 or \
                type(proof.get("code")) is not int or proof["code"] != 0 or proof.get("verificationRequired") is not False:
            raise SecondOutreachSourceError("invalid_resolution")
        oec = _id(row["oec_id"])
        if _id(proof.get("oecId")) != oec or _handle(proof.get("queriedHandle")) != _handle(row["lead_handle"]) or _handle(proof.get("returnedHandle")) != _handle(row["lead_handle"]):
            raise SecondOutreachSourceError("invalid_resolution")
        _text(row["lead_evidence_ref"]);_text(row["resolution_evidence_ref"])
        resolved_at = _timestamp(row["resolved_at"])
        if _timestamp(row["lead_observed_at"]) > resolved_at:
            raise SecondOutreachSourceError("invalid_resolution")
        creator_id = _text(row["creator_id"], 100)
        if not re.fullmatch(r"creator_[0-9a-f]{32}", creator_id):
            raise SecondOutreachSourceError("invalid_resolution")
        if row["handle_conflict"] not in (0, 1):
            raise SecondOutreachSourceError("invalid_resolution")
        conflict = row["handle_conflict"] == 1
        handle = None if conflict or row["current_handle"] is None else _handle(row["current_handle"])
        observed_at = _timestamp(row["last_observed_at"] if conflict else row.get("handle_observed_at") or row["last_observed_at"])
        return {"creatorId": creator_id, "oecId": oec, "handle": handle, "observedAt": observed_at}
    except SecondOutreachSourceError:
        raise
    except (KeyError, TypeError, ValueError):
        raise SecondOutreachSourceError("invalid_resolution") from None


def read_resolutions(path, external_ids):
    """Only this explicit source reference route is eligible; aliases are not queried."""
    path = Path(path)
    if not path.is_file():
        raise SecondOutreachSourceError("registry_not_imported")
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as database:
            database.row_factory = sqlite3.Row
            database.execute("PRAGMA query_only=ON");database.execute("BEGIN")
            if [row[0] for row in database.execute("SELECT version FROM identity_store_meta")] != [1]:
                raise SecondOutreachSourceError("registry_schema_mismatch")
            placeholders = ",".join("?" for _ in external_ids)
            rows = database.execute(f"""SELECT l.external_source,l.external_id,l.market,l.handle AS lead_handle,
                l.observed_at AS lead_observed_at,l.evidence_ref AS lead_evidence_ref,r.evidence_ref AS resolution_evidence_ref,
                r.observed_at AS resolved_at,r.exact_find_json,i.creator_id,i.market AS identity_market,i.oec_id,i.current_handle,
                i.handle_observed_at,i.last_observed_at,i.handle_conflict FROM handle_lead l JOIN handle_lead_resolution r USING(lead_id)
                JOIN creator_identity i ON i.creator_id=r.creator_id
                WHERE l.market='it' AND l.external_source='probe_source_reference' AND l.external_id IN ({placeholders})
                ORDER BY l.external_id,i.creator_id,r.observed_at,l.lead_id""", list(external_ids)).fetchall()
            result = []
            for row in rows:
                item = dict(row);item["exact_find"] = json.loads(item.pop("exact_find_json"));result.append(item)
            return result
    except SecondOutreachSourceError:
        raise
    except (sqlite3.Error, ValueError, TypeError):
        raise SecondOutreachSourceError("registry_schema_mismatch") from None


def build_second_source(document, resolutions, *, source_sha256):
    """Pure projection of already hash-verified source bytes and read-only resolutions."""
    if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise SecondOutreachSourceError("invalid_source_hash")
    if not isinstance(document, dict) or set(document) != {"dataset", "batch"}:
        raise SecondOutreachSourceError("invalid_source")
    dataset, batch = document["dataset"], document["batch"]
    if not isinstance(dataset, dict) or dataset.get("mode") != "imported-offline" or not isinstance(dataset.get("id"), str) or not dataset["id"].startswith("italy-pilot-") or \
            not isinstance(dataset.get("sourceRefs"), list) or not dataset["sourceRefs"] or not isinstance(batch, dict):
        raise SecondOutreachSourceError("invalid_source")
    for reference in dataset["sourceRefs"]:
        if not re.fullmatch(r".+ sha256:[0-9a-f]{64}", _text(reference)):
            raise SecondOutreachSourceError("missing_source")
    if any(batch.get(key) for key in ("offers", "demands")):
        raise SecondOutreachSourceError("unexpected_commercial_facts")
    raw_creators, raw_products, raw_edges = batch.get("creators"), batch.get("products"), batch.get("evidence")
    if not isinstance(raw_creators, list) or len(raw_creators) != 48 or not isinstance(raw_products, list) or not raw_products or not isinstance(raw_edges, list):
        raise SecondOutreachSourceError("invalid_source_cohort")
    creators, by_external = {}, {}
    for creator in raw_creators:
        external = creator.get("externalIdentity") if isinstance(creator, dict) else None
        if not isinstance(external, dict) or external.get("namespace") != "kalodata" or creator.get("market") != "it" or creator.get("oecId") is not None:
            raise SecondOutreachSourceError("invalid_source_namespace")
        creator_id, external_id = _text(creator.get("id"), 100), _id(external.get("id"))
        if creator_id in creators or external_id in by_external:
            raise SecondOutreachSourceError("duplicate_source_identity")
        creators[creator_id] = {"id": creator_id, "externalId": external_id, "handle": _handle(creator.get("name")), "source": _fact(creator.get("source"))}
        by_external[external_id] = creators[creator_id]
    products, product_ids = {}, set()
    for product in raw_products:
        if not isinstance(product, dict) or product.get("market") != "it":
            raise SecondOutreachSourceError("invalid_source_product")
        pid, product_id = _id(product.get("pid")), _text(product.get("id"), 100)
        if pid not in ITALIAN_PRODUCT_NAMES or pid in products or product_id in product_ids:
            raise SecondOutreachSourceError("invalid_source_product")
        product_ids.add(product_id)
        products[pid] = {"id": product_id, "pid": pid, "title": _text(product.get("title"), 300), "nameIt": ITALIAN_PRODUCT_NAMES[pid], "source": _fact(product.get("source"))}
    pairs, evidence_ids = {}, {}
    for edge in raw_edges:
        if not isinstance(edge, dict) or edge.get("market") != "it" or edge.get("creatorId") not in creators or edge.get("pid") not in products:
            raise SecondOutreachSourceError("invalid_source_edge")
        edge_id = _text(edge.get("id"), 160);units = _integer(edge.get("units"), positive=True);source = _fact(edge.get("source"), window=True)
        if edge_id in evidence_ids:
            if evidence_ids[edge_id] != _hash(edge):
                raise SecondOutreachSourceError("conflicting_source_evidence")
            continue
        evidence_ids[edge_id] = _hash(edge)
        pair = (edge["creatorId"], edge["pid"])
        observed = {"id": edge_id, "units": units, "source": source}
        if pair not in pairs or (source["observedAt"], units, edge_id) > (pairs[pair]["source"]["observedAt"], pairs[pair]["units"], pairs[pair]["id"]):
            pairs[pair] = observed
    mapped = {}
    for resolution in resolutions:
        external_id = _id(resolution.get("external_id"))
        if external_id not in by_external:
            continue
        recipient = _recipient(resolution)
        options = mapped.setdefault(external_id, {})
        existing = options.get(recipient["creatorId"])
        if existing and existing["oecId"] != recipient["oecId"]:
            raise SecondOutreachSourceError("conflicting_current_identity")
        if existing is None or recipient["observedAt"] > existing["observedAt"]:
            options[recipient["creatorId"]] = recipient
    opportunities = []
    for creator in sorted(creators.values(), key=lambda row: row["externalId"]):
        choices = []
        for (creator_id, pid), edge in pairs.items():
            if creator_id != creator["id"]:
                continue
            product, source = products[pid], edge["source"]
            choices.append({"id": product["id"], "pid": pid, "title": product["title"], "nameIt": product["nameIt"], "units": edge["units"],
                "windowStart": source["windowStart"], "windowEnd": source["windowEnd"], "windowBasis": source["windowBasis"],
                "observedAt": source["observedAt"], "sourceRef": source["ref"]})
        if not choices:
            raise SecondOutreachSourceError("source_creator_without_evidence")
        choices.sort(key=lambda row: (-row["units"], row["pid"]))
        options = sorted(mapped.get(creator["externalId"], {}).values(), key=lambda row: row["creatorId"])
        opportunity = {"id": "second-it-" + _hash({"market": "it", "namespace": "kalodata", "id": creator["externalId"]})[:24],
            "sourceCreatorId": creator["id"], "sourceHandle": creator["handle"], "sourceNamespace": "kalodata", "sourceExternalId": creator["externalId"],
            "currentRecipient": options[0] if len(options) == 1 else None,
            "recipientStatus": "verified" if len(options) == 1 else "ambiguous" if options else "unresolved",
            "recipientCandidates": options, "products": choices[:3], "omittedProducts": max(0, len(choices)-3), "historicalOwnership": "unverified"}
        opportunity["fingerprint"] = _hash({"sourceSha256": source_sha256, "creatorSource": creator["source"], "products": choices, "opportunity": opportunity})
        opportunities.append(opportunity)
    summary = {"opportunities": len(opportunities), "sourceProducts": len(products), "sourceEdges": len(evidence_ids), "uniquePairs": len(pairs),
        "selectedProducts": sum(len(row["products"]) for row in opportunities), "omittedProducts": sum(row["omittedProducts"] for row in opportunities),
        "currentRecipientsVerified": sum(row["recipientStatus"] == "verified" for row in opportunities),
        "unresolvedRecipients": sum(row["recipientStatus"] == "unresolved" for row in opportunities),
        "ambiguousRecipients": sum(row["recipientStatus"] == "ambiguous" for row in opportunities), "historicalOwnershipVerified": 0}
    candidates = sorted((row for row in opportunities if row["currentRecipient"]), key=lambda row: (-len(row["products"]), -max(p["units"] for p in row["products"]), row["id"]))[:3]
    return {"sourceFingerprint": _hash({"sourceSha256": source_sha256, "datasetId": dataset["id"], "opportunities": [{"id": row["id"], "fingerprint": row["fingerprint"]} for row in opportunities]}),
        "sourceSha256": source_sha256, "datasetId": dataset["id"], "opportunities": opportunities, "summary": summary,
        "candidatePurpose": "readiness_verification_only", "candidates": candidates}


def load_second_source(root=ROOT, *, expected_source_sha256=SOURCE_SHA256):
    """No writes. Hash mismatch requires deliberate source-version review."""
    root = Path(root).resolve();path = root / "var/italy-offline-batch.json"
    try:
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            raise SecondOutreachSourceError("source_not_imported")
        raw = path.read_bytes();actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_source_sha256:
            raise SecondOutreachSourceError("source_hash_mismatch")
        document = json.loads(raw)
        # Validate the whole cohort before selecting any registry references.
        checked = build_second_source(document, [], source_sha256=actual)
        external_ids = [row["sourceExternalId"] for row in checked["opportunities"]]
        resolutions = read_resolutions(root / "var/creator-identities.sqlite", external_ids)
        return build_second_source(document, resolutions, source_sha256=actual)
    except SecondOutreachSourceError:
        raise
    except (ValueError, TypeError, KeyError, OSError):
        raise SecondOutreachSourceError("invalid_source") from None
