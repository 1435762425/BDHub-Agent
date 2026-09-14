"""规则驱动的一键建链；使用筛选后货盘，执行前检查已有链接。"""
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import subprocess
from uuid import uuid4

from bdhub import config
from bdhub.hub.file_intents import now
from bdhub.hub.markets import require_capability
from bdhub.research.catalog_rules import filters_for, version, validate_link_rule
from bdhub.research.campaign_catalog import inventory_view
from .batches import BatchStore
from .service import TapLinkStore, source_snapshot, prepare_locked, validate_live
from .transport import account_for, transport_for
from .cleanup import CleanupStore


def existing_index(market, account, *, links=None, cleanup=None):
    link_store, clean_store = links or TapLinkStore(), cleanup or CleanupStore()
    result = {}
    seen = set(); complete_sources = set()
    for scan in clean_store.list(market):
        if scan["account"] != account:
            continue
        scope=scan.get('source_scope','all')
        source_types={1,2} if scope=='all' else {1 if scope=='campaign' else 2}
        for row in scan.get("items", []):
            if row.get('source') in complete_sources:
                continue
            lid = row["list_id"]
            if lid in seen:
                continue
            seen.add(lid)
            if row["state"] == "deleted" or clean_store.retirement_state(market, lid) == "deleted":
                continue
            for product in row.get("products", []):
                result.setdefault(product["pid"], {"list_id": lid, "url": row.get("url"), "reason": "已选 TapLink 中已有该 PID"})
        if scan.get("scan_complete"):
            complete_sources.update(source_types)
            if complete_sources=={1,2}:break
    for child in link_store.list(market):
        if child["account"] != account:
            continue
        lid = (child.get("card") or {}).get("list_id") or child.get("receipt", {}).get("list_id")
        if lid and clean_store.retirement_state(market, lid) == "deleted":
            continue
        if child.get("creation_attempted") or child.get("receipt", {}).get("url") or child["state"] in {"ready", "card_verified", "creating", "result_unknown", "created_unverified"}:
            result.setdefault(child["offer"]["pid"], {"list_id": lid, "url": child.get("receipt", {}).get("url"), "reason": "已有创建记录，跳过重复创建"})
    return result


def filtered_source(market, account, route, *, rules=None):
    kind = "joined_campaign" if route == "campaign" else "selected"
    source = source_snapshot(market, kind, account)
    return inventory_view(source, rules or filters_for(market)), source


def start_batch(body, *, store=None, source=None, rules=None, dispatch=True):
    market, route, account = body.get("market"), body.get("route"), body.get("account")
    if market not in {"mx", "br"} or route not in {"campaign", "selected"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(body.get("request_key") or "")):
        raise ValueError("taplink_scope_invalid")
    require_capability(market, "tap_link")
    account_for(market, account, check_maintenance=False)
    rule = validate_link_rule(body.get("rule"))
    limit = body.get("limit", 500)
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("taplink_batch_selection_required")
    filters = rules or filters_for(market)
    if body.get("filter_version") not in {None, version(filters)}:
        raise ValueError("taplink_filter_changed")
    view = inventory_view(source, filters) if source is not None else filtered_source(market, account, route, rules=filters)[0]
    if view["stale"] or not view["snapshot_id"]:
        raise ValueError("taplink_catalog_stale")
    repository = store or BatchStore()
    fingerprint = hashlib.sha256(json.dumps({"market": market, "account": account, "route": route, "rule": rule, "limit": limit}, sort_keys=True).encode()).hexdigest()
    key = hashlib.sha256(body["request_key"].encode()).hexdigest()
    with repository.lock():
        for prior in repository.list(market):
            if prior["request_hash"] == key:
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("taplink_request_key_conflict")
                return prior
            from .batches import public_batch
            if public_batch(prior)["state"] in {"queued", "running"}:
                raise ValueError("taplink_batch_running")
        batch = {"job_id": "tb_" + uuid4().hex, "workflow": "rules-v2", "market": market, "account": account, "route": route,
            "purpose": "catalog", "source_kind": "joined_campaign" if route == "campaign" else "selected", "snapshot_id": view["snapshot_id"],
            "link_rule": rule, "filter_rules": filters, "filter_version": version(filters), "limit": limit, "created_at": now(),
            "request_hash": key, "fingerprint": fingerprint, "state": "queued", "action": "create", "phase": "checking_existing", "error": "",
            "items": [{"pid": o["pid"], "offer_key": o["offer_key"], "product_name": o["product_name"], "campaign_id": o["campaign_id"], "state": "pending", "offer": o} for o in view["items"]]}
        repository.save(batch)
        if dispatch:
            try:
                process = subprocess.Popen([str(config.ROOT / ".venv/bin/python"), "-m", "bdhub.send.taplink.batch_worker", "--batch-id", batch["job_id"]],
                    cwd=config.ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                batch["worker_pid"] = process.pid
                repository.save(batch)
            except OSError:
                batch.update(state="paused", error="taplink_worker_start_failed")
                repository.save(batch)
                raise ValueError("taplink_worker_start_failed") from None
        return batch


def run_locked(batch, repository, *, links=None, factory=transport_for, current_view=None, current_rules=None, known=None):
    link_store = links or TapLinkStore()
    batch.update(state="running", phase="checking_existing")
    repository.save(batch)
    existing = known if known is not None else existing_index(batch["market"], batch["account"], links=link_store)
    attempts = 0
    def save():
        repository.save(batch)
    try:
        with factory(batch, allow_write=True) as transport:
            for index, item in enumerate(batch["items"]):
                if item["state"] != "pending":
                    continue
                if (repository.root / f"{batch['job_id']}.stop").exists():
                    batch.update(state="paused", error="taplink_batch_stopped")
                    break
                latest_rules = current_rules() if current_rules else filters_for(batch["market"])
                if version(latest_rules) != batch["filter_version"]:
                    raise ValueError("taplink_filter_changed")
                source = current_view() if current_view else filtered_source(batch["market"], batch["account"], batch["route"], rules=batch["filter_rules"])[0]
                if source["snapshot_id"] != batch["snapshot_id"] or source["stale"]:
                    raise ValueError("taplink_catalog_stale")
                prior = existing.get(item["pid"]) or transport.find_existing(item["pid"])
                if prior:
                    item.update(state="skipped_existing", list_id=prior.get("list_id"), url=prior.get("url"), error_message=prior["reason"])
                    save()
                    continue
                if attempts >= batch["limit"]:
                    break
                body = {k: batch[k] for k in ("market", "account", "route", "purpose", "source_kind", "snapshot_id", "link_rule", "filter_rules")}
                body.update(offer_key=item["offer_key"], request_key=batch["job_id"] + ":" + item["pid"], index=index + 1)
                child = None
                try:
                    with link_store.lock():
                        child = prepare_locked(body, item["offer"], link_store, link_store.list(batch["market"]))
                        if child.get("creation_attempted") or child["state"] in {"ready", "card_verified"}:
                            item.update(state="skipped_existing", error_message="已存在创建意图，跳过重复创建")
                            save()
                            continue
                        raw, missing = transport.offer(child)
                        validate_live(child, raw)
                        if missing:
                            raise ValueError("taplink_selected_source_required")
                        child.update(state="creating", stage="create", creation_attempted=True, worker_pid=os.getpid(), batch_id=batch["job_id"])
                        link_store.save(child)
                        item.update(job_id=child["job_id"], state="creating", creator_commission=child["creator_commission"], list_name=child["list_name"])
                        batch["phase"] = "creating"; save()
                        attempts += 1
                        child["receipt"] = transport.create(child)
                        child.update(state="verifying", stage="created")
                        link_store.save(child)
                        card = transport.card(child)
                        child.update(state="ready" if card else "created_unverified", card=card, error="" if card else "taplink_card_not_visible")
                        link_store.save(child)
                        item.update(state=child["state"], url=child["receipt"].get("url"), list_id=(card or {}).get("list_id") or child["receipt"].get("list_id"), error=child.get("error", ""))
                    if item["state"] == "created_unverified":
                        batch.update(state="paused", error="taplink_batch_verification_required")
                        save(); break
                except Exception as exc:
                    error = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("taplink_") else "taplink_operation_interrupted"
                    if child and child.get("creation_attempted"):
                        with link_store.lock():
                            child.update(state="created_unverified" if child.get("receipt", {}).get("url") else "result_unknown", error=error)
                            link_store.save(child)
                        item.update(state=child["state"], error=error)
                        batch.update(state="paused", error="taplink_batch_verification_required")
                        save(); break
                    item.update(state="failed", error=error)
                    if error in {"taplink_remote_read_failed", "taplink_operation_interrupted", "taplink_selected_read_failed"}:
                        raise
                save()
            if batch["state"] == "running":
                batch.update(state="completed", phase="completed")
    except Exception as exc:
        batch.update(state="paused", error=str(exc) if isinstance(exc, ValueError) and str(exc).startswith("taplink_") else "taplink_operation_interrupted")
    save()
    return batch
