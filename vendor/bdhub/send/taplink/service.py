"""推品商品卡准备意图；不写发送队列，不把 URL 当作 IM 可用证明。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from bdhub import config
from bdhub.research.campaign_catalog import CatalogStore, catalog_view, project_offer
from bdhub.hub.file_intents import FileIntentStore, now
from bdhub.send.sharelink.commission import CommissionEngine

BUSY_STATES = {"queued", "checking", "selecting", "creating", "verifying"}
UNKNOWN_STATES = {"selection_unknown", "result_unknown", "created_unverified"}
FACT_KEYS = ("pid", "campaign_id", "total_commission", "public_commission", "sample_quota", "end_date")


def fact_keys(job):
    return FACT_KEYS if job["purpose"] == "first" else tuple(k for k in FACT_KEYS if k != "sample_quota")


class TapLinkStore(FileIntentStore):
    def __init__(self, root: Path | None = None):
        super().__init__(root or config.ROOT / "data/send/taplinks", prefix="tl", error_prefix="taplink")

def source_snapshot(market, source_kind=None, account=None, *, catalog=None):
    if source_kind == "selected":
        from bdhub.research.product_source_store import SourceStore
        return SourceStore().selected_snapshot(market, account)
    repository = catalog or CatalogStore()
    return repository.joined_snapshot(market) if source_kind == "joined_campaign" else repository.snapshot(market)


def current_offer(market, purpose, offer_key, snapshot_id, *, catalog=None, source_kind=None, account=None, filter_rules=None):
    source = source_snapshot(market, source_kind, account, catalog=catalog)
    if purpose == "catalog":
        from bdhub.research.campaign_catalog import inventory_view
        from bdhub.research.catalog_rules import filters_for
        view = inventory_view(source, filter_rules or filters_for(market))
    else:
        view = catalog_view(source, purpose)
    if view["stale"] or view["snapshot_id"] != snapshot_id:
        raise ValueError("taplink_catalog_stale")
    offer = next((o for o in view["items"] if o["offer_key"] == offer_key), None)
    if offer is None:
        raise ValueError("taplink_offer_ineligible")
    return offer


def prepare(body, *, store=None, catalog=None):
    market, purpose = body.get("market"), body.get("purpose")
    if market not in {"br", "mx"} or purpose not in {"first", "second"}:
        raise ValueError("taplink_scope_invalid")
    if body.get("route") not in {"campaign", "selected"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(body.get("request_key") or "")):
        raise ValueError("taplink_parameters_invalid")
    from .transport import account_for
    account_for(market, body.get("account"), check_maintenance=False)
    source_kind = body.get("source_kind")
    if source_kind is not None and source_kind != {"campaign": "joined_campaign", "selected": "selected"}[body["route"]]:
        raise ValueError("taplink_source_route_mismatch")
    offer = current_offer(market, purpose, body.get("offer_key"), body.get("snapshot_id"), catalog=catalog, source_kind=source_kind, account=body.get("account"))
    repository = store or TapLinkStore()
    with repository.lock():
        return prepare_locked(body, offer, repository, repository.list(market))


def prepare_locked(body, offer, repository, existing):
    """调用方已验证来源/账号且持有意图锁；支持整批共用一次来源快照。"""
    market, source_kind = body["market"], body.get("source_kind")
    from bdhub.research.catalog_rules import engine_for, render_name
    from bdhub.send.sharelink.commission import decimal_value
    total, public = decimal_value(offer.get("total_commission")), decimal_value(offer.get("public_commission"))
    if total is None or public is None:
        raise ValueError("taplink_commission_invalid")
    commission = (engine_for(body["link_rule"]) if body.get("link_rule") else CommissionEngine()).calculate(total * 100, public * 100)
    if not commission.valid:
        raise ValueError("taplink_commission_invalid")
    request = {k: body[k] for k in ("market", "purpose", "route", "account", "offer_key", "snapshot_id")}
    if source_kind:
        request["source_kind"] = source_kind
    if body.get("link_rule"):
        request.update(link_rule=body["link_rule"], filter_rules=body["filter_rules"])
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    request_hash = hashlib.sha256(str(body["request_key"]).encode()).hexdigest()
    for old in existing:
        if old["request_hash"] == request_hash:
            if old["fingerprint"] != fingerprint:
                raise ValueError("taplink_request_key_conflict")
            return old
        same_product = old["offer"]["offer_key"] == offer["offer_key"]
        if same_product and (old["state"] in UNKNOWN_STATES | BUSY_STATES):
            from .cleanup import CleanupStore
            lid = (old.get("card") or {}).get("list_id") or old.get("receipt", {}).get("list_id")
            if lid and CleanupStore().retirement_state(market, lid) == "deleted":
                continue
            raise ValueError("taplink_existing_intent_requires_verification")
        if old["fingerprint"] == fingerprint and old["state"] in {"draft", "selected", "ready", "card_verified"}:
            from .cleanup import CleanupStore
            if old.get("card") and CleanupStore().is_blocked(market, old["card"]["list_id"]):
                continue
            return old
    job_id = "tl_" + uuid4().hex
    job = {**request, "job_id": job_id, "request_hash": request_hash, "fingerprint": fingerprint,
           "offer": offer, "creator_commission": str(commission.creator_pct), "agency_margin": str(commission.agency_margin_pct),
           "list_name": render_name(body["link_rule"], offer, commission.creator_pct, body.get("index", 1)) if body.get("link_rule") else "BDH-" + job_id[3:27], "state": "draft", "stage": "draft", "error": "",
           "receipt": {}, "card": None, "events": [], "created_at": now()}
    if body.get("link_rule"):
        from .protocol import create_payload
        create_payload(pid=offer["pid"], campaign_id=offer["campaign_id"], creator_pct=job["creator_commission"], name=job["list_name"], route=job["route"])
    repository.save(job)
    existing.append(job)
    return job


def validate_live(job, raw):
    if job.get("filter_rules"):
        from bdhub.research.campaign_catalog import project_inventory_offer
        from bdhub.research.catalog_rules import matches_filters
        offer = project_inventory_offer(raw, job["market"])
        if not offer or not offer["platform_available"] or not matches_filters(offer, job["filter_rules"]):
            raise ValueError("taplink_live_offer_ineligible")
        if offer["offer_key"] != job["offer"]["offer_key"] or any(offer[k] != job["offer"][k] for k in ("total_commission", "public_commission")):
            raise ValueError("taplink_offer_changed_repreview")
        return offer
    offer, reason = project_offer(raw, job["market"])
    if not offer or (job["purpose"] == "first" and not offer["first_eligible"]):
        raise ValueError("taplink_live_offer_ineligible")
    if any(offer[k] != job["offer"][k] for k in fact_keys(job)):
        raise ValueError("taplink_offer_changed_repreview")
    return offer


def public_job(job):
    result = {k: v for k, v in job.items() if k not in {"request_hash", "fingerprint", "worker_pid"}}
    list_id = (job.get("card") or {}).get("list_id") or job.get("receipt", {}).get("list_id")
    if list_id:
        from .cleanup import CleanupStore
        if CleanupStore().is_blocked(job["market"], list_id):
            result.update(state="retired", error="taplink_retired_for_cleanup")
    if job["state"] in BUSY_STATES:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(job["updated_at"])).total_seconds()
        if age > 15:
            pid = job.get("worker_pid")
            probe = subprocess.run(["ps", "-p", str(int(pid or 0)), "-o", "command="], capture_output=True, text=True, timeout=3)
            worker_module = "bdhub.send.taplink.batch_worker" if job.get("batch_id") else "bdhub.send.taplink.worker"
            worker_token = job.get("batch_id") or job["job_id"]
            if probe.returncode or worker_module not in probe.stdout or worker_token not in probe.stdout:
                state = "result_unknown" if job.get("creation_attempted") else "selection_unknown" if job.get("selection_attempted") else "failed"
                result.update(state=state, error="taplink_operation_interrupted")
    return result


def binding_for_send(market, account, pid, *, store=None, catalog=None):
    """已有推品准备记录的 PID 必须使用其已核验绑定；旧任务无记录时仍走原只读查卡。"""
    rows = [j for j in (store or TapLinkStore()).list(market) if j["offer"]["pid"] == pid]
    if not rows:
        return None
    latest = {}
    for job in rows:
        if job["account"] == account:
            latest.setdefault(job["offer"]["campaign_id"], job)
    scoped = list(latest.values())
    if not scoped or any(j["state"] not in {"ready", "card_verified", "failed"} for j in scoped):
        raise ValueError("taplink_card_preparation_required")
    bindings = {}
    for job in scoped:
        if job["state"] not in {"ready", "card_verified"}:
            continue
        from .cleanup import CleanupStore
        if CleanupStore().is_blocked(market, job["card"]["list_id"]):
            raise ValueError("taplink_retired_for_cleanup")
        if job.get("link_rule"):
            # 已建链接不因货盘换版失效；发送适配器仍逐次核验平台 PID/列表绑定。
            bindings[job["card"]["list_id"]] = job["card"]
            continue
        offer = current_offer(market, job["purpose"], job["offer_key"], job["snapshot_id"], catalog=catalog, source_kind=job.get("source_kind"), account=account)
        if any(offer[k] != job["offer"][k] for k in fact_keys(job)):
            raise ValueError("taplink_offer_changed_repreview")
        bindings[job["card"]["list_id"]] = job["card"]
    if len(bindings) != 1:
        raise ValueError("taplink_card_binding_not_unique")
    return next(iter(bindings.values()))


def launch(job_id, action, *, store=None):
    from bdhub.hub.markets import require_capability
    repository = store or TapLinkStore()
    with repository.lock():
        job = repository.read(job_id)
        if action == "create":
            require_capability(job["market"], "tap_link")
            if job["state"] not in {"draft", "selected"}:
                raise ValueError("taplink_intent_not_creatable")
            offer = current_offer(job["market"], job["purpose"], job["offer_key"], job["snapshot_id"], source_kind=job.get("source_kind"), account=job["account"], filter_rules=job.get("filter_rules"))
            if any(offer[k] != job["offer"][k] for k in fact_keys(job)):
                raise ValueError("taplink_offer_changed_repreview")
        elif action != "verify":
            raise ValueError("taplink_action_invalid")
        previous = job["state"]
        job.update(state="queued", action=action, previous_state=previous, error="")
        repository.save(job)
        try:
            process = subprocess.Popen([str(config.ROOT / ".venv/bin/python"), "-m", "bdhub.send.taplink.worker",
                                        "--job-id", job_id], cwd=config.ROOT, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            job["worker_pid"] = process.pid
            repository.save(job)
        except OSError:
            job.update(state=previous, error="taplink_worker_start_failed")
            repository.save(job)
            raise
    return job


def run(job_id, *, store=None, factory=None, catalog=None):
    from .transport import transport_for
    repository = store or TapLinkStore()
    with repository.lock():
        job = repository.read(job_id)
        if job["state"] != "queued":
            return job
        action = job["action"]
        def update(state, **fields):
            job.update(state=state, **fields)
            job["events"].append({"state": state, "at": now()})
            repository.save(job)
        try:
            update("checking")
            with (factory or transport_for)(job, allow_write=action == "create") as transport:
                if action == "create":
                    offer = current_offer(job["market"], job["purpose"], job["offer_key"], job["snapshot_id"], catalog=catalog, source_kind=job.get("source_kind"), account=job["account"], filter_rules=job.get("filter_rules"))
                    if any(offer[k] != job["offer"][k] for k in fact_keys(job)):
                        raise ValueError("taplink_offer_changed_repreview")
                    raw, needs_select = transport.offer(job)
                    validate_live(job, raw)
                    if needs_select:
                        raise ValueError("taplink_selected_source_required")
                    update("creating", stage="create", creation_attempted=True)
                    receipt = transport.create(job)
                    update("verifying", stage="created", receipt=receipt)
                elif job["stage"] == "select":
                    validate_live(job, transport.selected_offer(job))
                    update("selected", stage="selected", error="")
                    return job
                # 同账号 / 同列表 / 同 PID / 同 Campaign，创建回执后再次只读验证。
                card = transport.card(job)
                if card:
                    update("ready" if job["receipt"].get("url") else "card_verified", card=card, verified_at=now(), error="")
                elif job.get("creation_attempted"):
                    update("created_unverified" if job["receipt"].get("url") else "result_unknown", card=None, error="taplink_card_not_visible")
                else:
                    update(job.get("previous_state", "draft"), error="taplink_not_created")
        except Exception as exc:
            code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("taplink_") else "taplink_operation_interrupted"
            if job["stage"] in {"create", "created"}:
                state = "created_unverified" if job["receipt"].get("url") else "result_unknown"
            elif job["stage"] == "select":
                state = "selection_unknown"
            else:
                state = "failed" if action == "create" else job.get("previous_state", "draft")
            update(state, error=code)
        return job
