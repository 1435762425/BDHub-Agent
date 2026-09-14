"""已加入 Campaign 的去敏日更索引文件。"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bdhub.config import ROOT


SCHEMA_VERSION = 1
DEFAULT_MAX_AGE = timedelta(hours=36)
MAX_COMPRESSED_BYTES = 100 * 1024 * 1024
MAX_JSON_BYTES = 500 * 1024 * 1024
_PID_RE = re.compile(r"^[0-9]{10,32}$")


class JoinedCampaignCacheError(RuntimeError):
    pass


def partner_identity_hash(partner_id: object) -> str:
    clean = str(partner_id or "").strip()
    if not clean:
        raise ValueError("joined_campaign_partner_missing")
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def default_cache_path(partner_id: object) -> Path:
    fingerprint = partner_identity_hash(partner_id)[:16]
    return ROOT / "data" / "runtime" / "sharelink" / f"joined-campaign-{fingerprint}.json.gz"


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_time(value: object, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise JoinedCampaignCacheError(f"joined_campaign_cache_{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise JoinedCampaignCacheError(f"joined_campaign_cache_{field}_invalid")
    return parsed.astimezone(timezone.utc)


def _validated_index(value: object) -> tuple[dict[str, list[dict]], int]:
    if not isinstance(value, dict) or len(value) > 2_000_000:
        raise JoinedCampaignCacheError("joined_campaign_cache_index_invalid")
    index: dict[str, list[dict]] = {}
    row_count = 0
    for pid, raw_items in value.items():
        clean_pid = str(pid)
        if not _PID_RE.fullmatch(clean_pid) or not isinstance(raw_items, list):
            raise JoinedCampaignCacheError("joined_campaign_cache_index_invalid")
        items: list[dict] = []
        seen_campaigns: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, dict) or raw.get("_source_pool") != "joined_campaign":
                raise JoinedCampaignCacheError("joined_campaign_cache_item_invalid")
            product = raw.get("campaign_product") or {}
            campaign = raw.get("campaign_info") or {}
            if not isinstance(product, dict) or not isinstance(campaign, dict):
                raise JoinedCampaignCacheError("joined_campaign_cache_item_invalid")
            if str(product.get("product_id") or "") != clean_pid:
                raise JoinedCampaignCacheError("joined_campaign_cache_item_invalid")
            try:
                approved = int(float(str(product.get("product_status") or "0"))) == 2
            except (TypeError, ValueError):
                approved = False
            campaign_id = str(campaign.get("campaign_id") or "").strip()
            if not approved or not campaign_id:
                raise JoinedCampaignCacheError("joined_campaign_cache_item_invalid")
            if campaign_id in seen_campaigns:
                continue
            seen_campaigns.add(campaign_id)
            items.append(raw)
            row_count += 1
            if row_count > 5_000_000:
                raise JoinedCampaignCacheError("joined_campaign_cache_too_large")
        index[clean_pid] = items
    return index, row_count


def write_joined_campaign_cache(
    *,
    partner_id: object,
    index: dict[str, list[dict]],
    campaign_count: int,
    product_pages_scanned: int,
    path: str | Path | None = None,
    generated_at: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> dict:
    clean_index, row_count = _validated_index(index)
    generated = _utc(generated_at)
    expires = generated + max_age
    payload = {
        "market": "mx",
        "partner_identity_hash": partner_identity_hash(partner_id),
        "generated_at": generated.isoformat(),
        "expires_at": expires.isoformat(),
        "campaign_count": max(0, int(campaign_count)),
        "product_pages_scanned": max(0, int(product_pages_scanned)),
        "pid_count": len(clean_index),
        "row_count": row_count,
        "index": clean_index,
    }
    payload_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload_bytes) > MAX_JSON_BYTES:
        raise JoinedCampaignCacheError("joined_campaign_cache_too_large")
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "payload": payload,
    }
    target = Path(path) if path is not None else default_cache_path(partner_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as compressed:
                compressed.write(json.dumps(
                    envelope,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    return {
        "path": str(target),
        "generated_at": generated,
        "expires_at": expires,
        "campaign_count": payload["campaign_count"],
        "pid_count": len(clean_index),
        "row_count": row_count,
        "product_pages_scanned": payload["product_pages_scanned"],
    }


def load_joined_campaign_cache(
    *,
    partner_id: object,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> dict:
    target = Path(path) if path is not None else default_cache_path(partner_id)
    try:
        if target.stat().st_size > MAX_COMPRESSED_BYTES:
            raise JoinedCampaignCacheError("joined_campaign_cache_too_large")
        with gzip.open(target, "rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
    except FileNotFoundError as exc:
        raise JoinedCampaignCacheError("joined_campaign_cache_missing") from exc
    except (OSError, EOFError) as exc:
        raise JoinedCampaignCacheError("joined_campaign_cache_unreadable") from exc
    if len(raw) > MAX_JSON_BYTES:
        raise JoinedCampaignCacheError("joined_campaign_cache_too_large")
    try:
        envelope = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise JoinedCampaignCacheError("joined_campaign_cache_invalid_json") from exc
    if not isinstance(envelope, dict) or envelope.get("schema_version") != SCHEMA_VERSION:
        raise JoinedCampaignCacheError("joined_campaign_cache_schema_invalid")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise JoinedCampaignCacheError("joined_campaign_cache_payload_invalid")
    payload_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checksum = str(envelope.get("payload_sha256") or "")
    if not hmac.compare_digest(checksum, hashlib.sha256(payload_bytes).hexdigest()):
        raise JoinedCampaignCacheError("joined_campaign_cache_checksum_invalid")
    if payload.get("market") != "mx" or payload.get(
        "partner_identity_hash"
    ) != partner_identity_hash(partner_id):
        raise JoinedCampaignCacheError("joined_campaign_cache_identity_mismatch")
    generated = _parse_time(payload.get("generated_at"), field="generated_at")
    expires = _parse_time(payload.get("expires_at"), field="expires_at")
    current = _utc(now)
    if generated > current + timedelta(minutes=5):
        raise JoinedCampaignCacheError("joined_campaign_cache_from_future")
    if current >= expires:
        raise JoinedCampaignCacheError("joined_campaign_cache_expired")
    index, row_count = _validated_index(payload.get("index"))
    if int(payload.get("pid_count") or -1) != len(index) or int(
        payload.get("row_count") or -1
    ) != row_count:
        raise JoinedCampaignCacheError("joined_campaign_cache_count_mismatch")
    return {
        "path": str(target),
        "generated_at": generated,
        "expires_at": expires,
        "campaign_count": max(0, int(payload.get("campaign_count") or 0)),
        "pid_count": len(index),
        "row_count": row_count,
        "product_pages_scanned": max(
            0, int(payload.get("product_pages_scanned") or 0),
        ),
        "index": index,
    }


__all__ = [
    "DEFAULT_MAX_AGE",
    "JoinedCampaignCacheError",
    "default_cache_path",
    "load_joined_campaign_cache",
    "partner_identity_hash",
    "write_joined_campaign_cache",
]
