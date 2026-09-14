"""批量建链共用单商品意图；一次确认，逐 PID 留存真实结果。"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from bdhub import config
from bdhub.hub.file_intents import FileIntentStore, now
from bdhub.hub.markets import require_capability
from bdhub.research.campaign_catalog import catalog_view
from . import service
from .transport import account_for


class BatchStore(FileIntentStore):
    def __init__(self, root: Path | None = None):
        super().__init__(root or config.ROOT / "data/send/taplink-batches", prefix="tb", error_prefix="taplink_batch")


def prepare_batch(body, *, store=None, links=None, source=None):
    market, route, purpose = body.get("market"), body.get("route"), body.get("purpose")
    if market not in {"mx", "br"} or route not in {"campaign", "selected"} or purpose not in {"first", "second"}:
        raise ValueError("taplink_scope_invalid")
    account_for(market, body.get("account"), check_maintenance=False)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(body.get("request_key") or "")):
        raise ValueError("taplink_parameters_invalid")
    keys = body.get("offer_keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= 500 or any(not isinstance(k, str) or len(k) > 60 for k in keys):
        raise ValueError("taplink_batch_selection_required")
    keys = list(dict.fromkeys(keys))
    source_kind = "joined_campaign" if route == "campaign" else "selected"
    snapshot = source if source is not None else service.source_snapshot(market, source_kind, body["account"])
    view = catalog_view(snapshot, purpose)
    if view["stale"] or view["snapshot_id"] != body.get("snapshot_id"):
        raise ValueError("taplink_catalog_stale")
    offers = {o["offer_key"]: o for o in view["items"]}
    request = {k: body[k] for k in ("market", "account", "route", "purpose", "snapshot_id")}
    fingerprint = hashlib.sha256(json.dumps({**request, "offer_keys": sorted(keys)}, sort_keys=True).encode()).hexdigest()
    request_hash = hashlib.sha256(body["request_key"].encode()).hexdigest()
    repository, link_store = store or BatchStore(), links or service.TapLinkStore()
    with repository.lock():
        for prior in repository.list(market):
            if prior["request_hash"] == request_hash:
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("taplink_request_key_conflict")
                return prior
        batch = {**request, "source_kind": source_kind, "job_id": "tb_" + uuid4().hex, "created_at": now(),
                 "fingerprint": fingerprint, "request_hash": request_hash, "offer_keys": keys, "state": "preparing", "error": "", "items": []}
        with link_store.lock():
            repository.save(batch)
            existing = link_store.list(market)
            for key in keys:
                item = {"offer_key": key, "pid": key.split(":", 1)[0], "state": "skipped", "error": "taplink_offer_ineligible"}
                offer = offers.get(key)
                if offer:
                    item.update(product_name=offer["product_name"], campaign_id=offer["campaign_id"])
                    try:
                        child = service.prepare_locked({**request, "source_kind": source_kind, "offer_key": key,
                            "request_key": batch["job_id"] + ":" + key}, offer, link_store, existing)
                        item.update(job_id=child["job_id"], state=child["state"], error=child.get("error", ""),
                                    creator_commission=child["creator_commission"], agency_margin=child["agency_margin"],
                                    url=child.get("receipt", {}).get("url"), list_id=(child.get("card") or {}).get("list_id") or child.get("receipt", {}).get("list_id"))
                    except ValueError as exc:
                        item.update(state="blocked", error=str(exc))
                batch["items"].append(item)
        batch["state"] = "previewed"
        repository.save(batch)
        return batch


def launch(batch_id, action, *, store=None):
    repository = store or BatchStore()
    with repository.lock():
        batch = repository.read(batch_id)
        if action == "create":
            require_capability(batch["market"], "tap_link")
            if batch["state"] not in {"previewed", "paused"} or any(i["state"] in service.UNKNOWN_STATES | {"creating", "checking", "queued"} for i in batch["items"]):
                raise ValueError("taplink_batch_verification_required")
            (repository.root / f"{batch_id}.stop").unlink(missing_ok=True)
        elif action != "verify":
            raise ValueError("taplink_action_invalid")
        batch.update(state="queued", action=action, error="")
        repository.save(batch)
        try:
            process = subprocess.Popen([str(config.ROOT / ".venv/bin/python"), "-m", "bdhub.send.taplink.batch_worker", "--batch-id", batch_id],
                                       cwd=config.ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            batch["worker_pid"] = process.pid
            repository.save(batch)
        except OSError:
            batch.update(state="paused", error="taplink_worker_start_failed")
            repository.save(batch)
            raise
    return batch


def run_batch(batch_id, *, store=None, links=None, runner=service.run):
    repository, link_store = store or BatchStore(), links or service.TapLinkStore()
    with repository.lock():
        batch = repository.read(batch_id)
        if batch["state"] != "queued":
            return batch
        if batch.get("workflow") == "rules-v2" and batch["action"] == "create":
            from .rule_batches import run_locked
            return run_locked(batch, repository, links=link_store)
        action = batch["action"]
        batch["state"] = "running"
        repository.save(batch)
        try:
            for item in batch["items"]:
                if action == "create" and (repository.root / f"{batch_id}.stop").exists():
                    batch.update(state="paused", error="taplink_batch_stopped")
                    break
                if not item.get("job_id"):
                    continue
                with link_store.lock():
                    child = link_store.read(item["job_id"])
                    allowed = child["state"] in {"draft", "selected"} if action == "create" else child["state"] in service.UNKNOWN_STATES | service.BUSY_STATES
                    if not allowed:
                        item.update(state=child["state"], error=child.get("error", ""))
                        if child["state"] in service.BUSY_STATES | service.UNKNOWN_STATES:
                            batch.update(state="paused", error="taplink_batch_verification_required")
                            break
                        continue
                    if action == "create" and child.get("creation_attempted"):
                        raise ValueError("taplink_batch_verification_required")
                    child.update(state="queued", action=action, previous_state=child["state"], worker_pid=os.getpid(), batch_id=batch_id)
                    link_store.save(child)
                # 单商品意图始终是创建事实源；这里不另发第二条创建请求。
                item["state"] = "creating" if action == "create" else "verifying"
                repository.save(batch)
                result = runner(child["job_id"], store=link_store)
                item.update(state=result["state"], error=result.get("error", ""), url=result.get("receipt", {}).get("url"),
                            list_id=(result.get("card") or {}).get("list_id") or result.get("receipt", {}).get("list_id"))
                repository.save(batch)
                if result["state"] in service.UNKNOWN_STATES or result.get("error") in {
                    "taplink_catalog_stale", "taplink_remote_read_failed", "taplink_selected_read_failed",
                    "taplink_account_maintenance_due", "taplink_operation_interrupted"}:
                    batch.update(state="paused", error="taplink_batch_verification_required")
                    break
            else:
                batch["state"] = "previewed" if any(i["state"] in {"draft", "selected"} for i in batch["items"]) else "completed"
        except Exception as exc:
            batch.update(state="paused", error=str(exc) if isinstance(exc, ValueError) and str(exc).startswith("taplink_") else "taplink_operation_interrupted")
        repository.save(batch)
        return batch


def public_batch(batch):
    result = {k: v for k, v in batch.items() if k not in {"worker_pid", "request_hash", "fingerprint"}}
    messages = {"taplink_offer_ineligible": "商品不在当前来源或已不符合筛选条件", "taplink_catalog_stale": "货盘已更新，请按新货盘重新开始",
        "taplink_commission_invalid": "按当前算法计算的佣金超出平台允许范围，已跳过", "taplink_live_offer_ineligible": "最新商品或活动状态不符合已保存筛选规则",
        "taplink_filter_changed": "货盘筛选规则已变更，请按最新规则重新开始",
        "taplink_existing_intent_requires_verification": "已有任务或未知结果，先回查原意图", "taplink_offer_changed_repreview": "佣金或活动条件已变化",
        "taplink_create_result_unknown": "创建结果未知，需先回查", "taplink_card_not_visible": "链接已创建，商品卡尚待核验",
        "taplink_selected_source_required": "请先到商品源完成选入", "taplink_operation_interrupted": "账号或平台请求中断，请检查后回查"}
    result["items"] = [{**{k: v for k, v in item.items() if k != "offer"}, "error_message": messages.get(item.get("error"), "该项需要检查，错误代码：" + str(item.get("error"))) if item.get("error") else item.get("error_message", "")} for item in batch["items"]]
    from .cleanup import CleanupStore
    for item in result["items"]:
        if item.get("list_id") and CleanupStore().is_blocked(batch["market"], item["list_id"]):
            item.update(state="retired", error_message="该列表已进入清洗或已删除，不能继续使用")
    result["counts"] = {"total": len(batch["items"]),
        "ready": sum(i["state"] in {"ready", "card_verified"} for i in result["items"]),
        "pending": sum(i["state"] in {"draft", "selected", "pending"} for i in batch["items"]),
        "skipped_existing": sum(i["state"] == "skipped_existing" for i in batch["items"]),
        "processed": sum(i["state"] not in {"draft", "selected", "pending", "creating"} for i in batch["items"]),
        "failed": sum(i["state"] in {"failed", "blocked", "skipped", "retired"} for i in result["items"]),
        "unknown": sum(i["state"] in service.UNKNOWN_STATES for i in result["items"])}
    if batch["state"] in {"queued", "running", "preparing"} and (datetime.now(timezone.utc) - datetime.fromisoformat(batch["updated_at"])).total_seconds() > 20:
        probe = subprocess.run(["ps", "-p", str(int(batch.get("worker_pid") or 0)), "-o", "command="], capture_output=True, text=True, timeout=3)
        if probe.returncode or "bdhub.send.taplink.batch_worker" not in probe.stdout or batch["job_id"] not in probe.stdout:
            result.update(state="paused", error="taplink_batch_verification_required")
    if batch.get("workflow") == "rules-v2":
        result["items"] = [i for i in result["items"] if i["state"] in {"failed", "blocked", "result_unknown", "created_unverified"}][:100]
    return result
