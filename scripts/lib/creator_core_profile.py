"""TikTok core-profile adapters and bounded refresh plans.

No transport, queues or database writes. Callers must keep the original account,
canonical market/OEC and observation time when later persisting these facts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

from lib.market_identity import validated_report_targets
from lib.profile_completion import merge_profile_summaries, summarize_profile
from lib.creator_identity import valid_handle


CORE_FIELDS = ("med_gmv_revenue", "industry_groups", "video_gmv", "live_gmv")
_IDENTITY_FIELDS = ("handle", "creator_oecuid", "selection_region")
_END = "sales_performance_end_time"
_AVAILABLE = {"value", "zero"}
_FIND_MARKETS = {"br", "my", "th"}
_PROFILE_MARKETS = {"it", "de", "fr", "uk"}


def core_read_route(market):
    if market in _FIND_MARKETS:
        return "find"
    if market in _PROFILE_MARKETS:
        return "profile"
    raise ValueError("core_profile_market_unsupported")


def _observed_at(value):
    if not isinstance(value, str):
        raise ValueError("core_profile_observation_time_missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("core_profile_observation_time_missing")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _field(name, field):
    """Re-normalize retained summaries instead of trusting arbitrary nested values."""
    state = field["status"]
    if state not in _AVAILABLE:
        return {"status": state}
    value = field.get("value")
    if name in {"med_gmv_revenue", "video_gmv", "live_gmv"}:
        if not isinstance(value, dict):
            return {"status": "error"}
        raw = {}
        for src, dst in (("decimal", "value"), ("minimum", "minimal"), ("maximum", "maximum"),
                         ("rawSymbol", "symbol"), ("format", "format"),
                         ("minimumFormat", "minimal_format"), ("maximumFormat", "maximum_format")):
            if src in value:
                raw[dst] = value[src]
        value = raw
    clean = summarize_profile({name: {"status": 0, "is_authorized": True, "value": value}})["fields"][name]
    if clean["status"] in _AVAILABLE and clean["status"] != state:
        return {"status": "error"}
    return clean


def core_summary(summary, *, market, oec_id, handle):
    """Keep the three requested dimensions plus their TikTok identity/end date."""
    merge_profile_summaries([summary])  # Validate shape and identity/field consistency.
    fields = summary["fields"]
    clean = {name: _field(name, fields[name]) for name in (*_IDENTITY_FIELDS, *CORE_FIELDS, _END)}
    identity = summarize_profile({name: clean[name].get("value") for name in _IDENTITY_FIELDS})["identity"]
    if identity != {"market": market, "oecId": oec_id, "handle": handle}:
        raise ValueError("core_profile_identity_mismatch")
    return {"identity": identity, "fields": clean,
            "availableFields": [name for name, field in clean.items() if field["status"] in _AVAILABLE]}


def find_core_observations(report, requested, *, market, account, expected_oecs=None):
    """Preview safe Find facts, including a confirmed prefix before Profile fails.

    expected_oecs is mandatory for refreshing known creators via mutable handles.
    This method never imports facts or changes identity/outreach state.
    """
    schema = "bdhub.italy-profile-probe.v3" if market == "it" else "bdhub.market-profile-probe.v1"
    if report.get("schema") != schema or \
            type(report.get("oldDatabaseWrites")) is not int or type(report.get("realSends")) is not int:
        raise ValueError("core_profile_report_invalid")
    if len({source["ref"] for source in requested}) != len(requested):
        raise ValueError("core_profile_duplicate_request")
    valid, _, _ = validated_report_targets(report, requested, market, account)
    observed_at = _observed_at(report.get("finishedAt"))
    fingerprint = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result = []
    for target, source, _ in valid:
        if target.get("status") == "unresolved":
            continue
        ref = target["targetRef"]
        receipts = [row for row in report["requests"] if row.get("targetRef") == ref and row.get("stage") == "find"]
        if len(receipts) != 1 or receipts[0].get("systemError3") is True or \
                target.get("externalId") != source.get("sourceId"):
            raise ValueError("core_profile_report_invalid")
        found = target["find"]
        identity = found["identity"]
        oec = identity["oecId"]
        if expected_oecs is not None and expected_oecs.get(ref) != oec:
            raise ValueError("core_profile_identity_mismatch")
        payload = core_summary(found, market=market, oec_id=oec, handle=source["handle"])
        complete = all(payload["fields"][name]["status"] in _AVAILABLE for name in CORE_FIELDS)
        payload["profileSource"] = {"provider": "tiktok", "method": "find", "account": account,
                                    "windowStart": None, "salesPerformanceEnd": payload["fields"][_END]}
        result.append({"market": market, "account": account, "oecId": oec, "handle": source["handle"],
                       "targetRef": ref, "observedAt": observed_at, "coreComplete": complete,
                       "evidenceRef": f"market-profile-probe:{fingerprint}:{ref}:find-profile", "payload": payload})
    return result


def core_refresh_plan(market, creators):
    """Build at most three read targets; execute only through the existing probe.

    Find markets omit oecId in the actual request to select the tested Find route,
    but retain expectedOecs for strict readback. A recycled handle cannot overwrite
    the original creator. Profile markets continue to use a stable OEC directly.
    """
    if market not in _FIND_MARKETS | _PROFILE_MARKETS:
        raise ValueError("core_profile_market_unsupported")
    if not isinstance(creators, list) or not 1 <= len(creators) <= 3:
        raise ValueError("core_profile_target_count_invalid")
    targets, expected, seen, creator_ids = [], {}, set(), set()
    for creator in creators:
        oec, creator_id, handle = creator.get("oec_id"), creator.get("creator_id"), creator.get("current_handle")
        if creator.get("market") != market or not isinstance(oec, str) or not re.fullmatch(r"[0-9]{1,40}", oec) or \
                not isinstance(creator_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", creator_id) or \
                oec in seen or creator_id in creator_ids:
            raise ValueError("core_profile_identity_mismatch")
        seen.add(oec)
        creator_ids.add(creator_id)
        ref = "core-profile:" + creator_id
        target = {"ref": ref, "externalId": creator_id}
        if market in _FIND_MARKETS:
            if not valid_handle(handle, maximum=100):
                raise ValueError("core_profile_current_handle_required")
            target["handle"] = handle
        else:
            target["oecId"] = oec
        targets.append(target)
        expected[ref] = oec
    probe = {"market": market, "identityOnly": market in _FIND_MARKETS, "targets": targets}
    if market in _PROFILE_MARKETS:
        probe["profileTypeSets"] = [[2]]
    return {"probeInput": probe, "expectedOecs": expected, "portraitSource": "tiktok",
            "canonicalIdentityKey": "market+oec_id", "executionStarted": False}
