"""历史 Product List TapLink 扫描与清理；只删除重新确认完全失效的列表。"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from bdhub import config
from bdhub.hub.file_intents import FileIntentStore, atomic_json, now
from bdhub.hub.markets import require_capability
from bdhub.send.sharelink.candidates import campaign_time_status
from bdhub.research.product_source_transport import SourceTransport
from .transport import TapLinkTransport, transport_context, account_for
from .protocol import identifier
from .service import TapLinkStore

LIST_PATH = "/api/v1/affiliate/partner/campaign/product_list/list"
PRODUCTS_PATH = "/api/v1/affiliate/partner/campaign/product_list/products"
DELETE_PATH = "/api/v1/affiliate/partner/campaign/product_list/delete"
UNAVAILABLE = {1: "商品失效", 2: "店铺退出活动", 3: "店铺关闭", 4: "商品被治理", 5: "店铺被治理", 6: "商家下架", 7: "系统下架", 9: "商品被平台限制"}


def product_health(product, campaign=None):
    if product.get("is_under_governed") is True:
        return "invalid", "商品被治理"
    try:
        unavailable = int(product.get("unavailable_type") or 0)
        status = int(product.get("product_status", 0))
    except (ValueError, TypeError):
        return "unknown", "平台状态字段无法识别"
    if unavailable in UNAVAILABLE:
        return "invalid", UNAVAILABLE[unavailable]
    if status in {3, 5}:
        return "invalid", "商品已关闭/下架" if status == 5 else "商品未通过审核"
    if campaign and campaign_time_status(campaign)[0] == "EXPIRED":
        return "invalid", "对应 Campaign 已结束"
    if unavailable not in {0, 8} or status != 2:
        return "unknown", "商品状态待确认"
    if unavailable == 8 or str(product.get("stock")) == "0":
        return "valid", "暂时缺货，保留链接"
    return "valid", "商品仍可用"


def classify_list(row, products, *, campaigns=None):
    if len(products) != row["product_total"]:
        return {"state": "unknown", "reason": "列表数量与详情不一致，需要回查", "products": [], "eligible": False}
    facts = []
    for product in products:
        pid = str(product.get("product_id") or "")
        if not re.fullmatch(r"[0-9]{19}", pid):
            return {"state": "unknown", "reason": "详情缺少精确 PID", "products": [], "eligible": False}
        cid = str(product.get("campaign_id") or row.get("campaign_id") or "")
        campaign = (campaigns or {}).get(cid)
        state, reason = product_health(product, campaign)
        facts.append({"pid": pid, "name": str(product.get("product_name") or ""), "state": state, "reason": reason,
                      "campaign_id": cid, "product_status": product.get("product_status"), "unavailable_type": product.get("unavailable_type"), "governed": product.get("is_under_governed") is True})
    states = {p["state"] for p in facts}
    if not facts:
        state, reason = "invalid", "空商品列表"
    elif states == {"invalid"}:
        state, reason = "invalid", "；".join(dict.fromkeys(p["reason"] for p in facts))
    elif "unknown" in states:
        state, reason = "unknown", "包含状态未确认商品，暂不删除"
    elif "invalid" in states:
        state, reason = "mixed", "仍包含可用商品，保留整条列表"
    else:
        state, reason = "valid", "商品仍可用，保留链接"
    fingerprint = hashlib.sha256(json.dumps({"id": row["list_id"], "campaign": row["campaign_id"], "source": row["source"], "products": facts}, sort_keys=True).encode()).hexdigest()
    return {"state": state, "reason": reason, "products": facts, "eligible": state == "invalid", "fingerprint": fingerprint}


class CleanupStore(FileIntentStore):
    def __init__(self, root: Path | None = None):
        super().__init__(root or config.ROOT / "data/send/taplink-cleanup", prefix="tc", error_prefix="taplink_cleanup")

    def block(self, market, list_id, state):
        identifier(list_id)
        if market not in {"mx", "br", "it", "uk", "us", "jp", "de"}:
            raise ValueError("taplink_scope_invalid")
        path = self.root / "retired" / f"{market}-{list_id}.json"
        atomic_json(path, {"state": state, "at": now()})
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def is_blocked(self, market, list_id):
        return self.retirement_state(market, list_id) is not None

    def retirement_state(self, market, list_id):
        if not re.fullmatch(r"[1-9][0-9]{0,31}", str(list_id)):
            return None
        path = self.root / "retired" / f"{market}-{list_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text()).get("state") or "delete_unknown"
        except (OSError, ValueError):
            return "delete_unknown"


class CleanupTransport(TapLinkTransport):
    READ_ENDPOINTS = TapLinkTransport.READ_ENDPOINTS | {(LIST_PATH, "GET"), (PRODUCTS_PATH, "GET")}
    WRITE_ENDPOINTS = {(DELETE_PATH, "POST")}

    def _xhr(self, **kwargs):
        if getattr(self, "check_stop", None):
            self.check_stop()
        return super()._xhr(**kwargs)

    def campaign_catalog(self):
        # 全部已加入活动，包含已结束活动；不使用只保留进行中的商品采集过滤。
        rows = SourceTransport.campaigns(self, {"campaign_join_status_category": "1", "crs_campaign_types": ""})
        return {str(c["campaign_id"]): c for c in rows}

    def lists_in_scope(self, source, campaign_id):
        rows, seen = [], set()
        total = None
        for page in range(1, 1001):
            result = self._xhr(method="GET", path=LIST_PATH, params=self._params() | {
                "campaign_id": campaign_id, "source": source, "cur_page": page, "page_size": 100}, payload=None, write=False)
            data = self.require_read(result).get("data")
            if not isinstance(data, dict) or type(data.get("total")) is not int or data["total"] < 0:
                raise ValueError("taplink_cleanup_inventory_malformed")
            if total is not None and total != data["total"]:
                raise ValueError("taplink_cleanup_inventory_changed")
            total = data["total"]
            batch = data.get("lists", [])
            if not isinstance(batch, list):
                raise ValueError("taplink_cleanup_inventory_malformed")
            for raw in batch:
                lid = identifier(str(raw.get("id") or ""))
                if lid in seen or type(raw.get("total")) is not int or raw["total"] < 0:
                    raise ValueError("taplink_cleanup_inventory_incomplete")
                seen.add(lid)
                rows.append({"list_id": lid, "name": str(raw.get("name") or ""), "url": str(raw.get("url") or ""),
                             "source": source, "campaign_id": campaign_id, "product_total": raw["total"], "platform_updated_at": raw.get("update_time")})
            if len(rows) == total:
                return rows
            if len(batch) < 100 or len(rows) > total:
                raise ValueError("taplink_cleanup_inventory_incomplete")
        raise ValueError("taplink_cleanup_inventory_limit")

    def products(self, row):
        products, seen, cursors = [], set(), set()
        cursor = None
        total = None
        for _ in range(101):
            params = self._params() | {"list_id": row["list_id"], "source": row["source"]}
            if cursor is not None:
                params["cursor"] = cursor
            result = self._xhr(method="GET", path=PRODUCTS_PATH, params=params, payload=None, write=False)
            data = self.require_read(result).get("data")
            if not isinstance(data, dict) or type(data.get("total_num")) is not int or data["total_num"] < 0:
                raise ValueError("taplink_cleanup_products_malformed")
            if total is not None and total != data["total_num"]:
                raise ValueError("taplink_cleanup_products_changed")
            total = data["total_num"]
            rows = data.get("campaign_products", [])
            if not isinstance(rows, list):
                raise ValueError("taplink_cleanup_products_malformed")
            for product in rows:
                key = (str(product.get("product_id") or ""), str(product.get("campaign_id") or row["campaign_id"]))
                if key in seen:
                    raise ValueError("taplink_cleanup_products_incomplete")
                seen.add(key)
                products.append(product)
            if len(products) == total:
                return products
            cursor = data.get("next_cursor")
            if not rows or not cursor or str(cursor) in cursors or len(products) > total:
                raise ValueError("taplink_cleanup_products_incomplete")
            cursors.add(str(cursor))
        raise ValueError("taplink_cleanup_products_limit")

    def delete_list(self, list_id):
        return self._xhr(method="POST", path=DELETE_PATH, params=self._params(), payload={"list_id": identifier(list_id)}, write=True)


def cleanup_transport(job, *, allow_write=False):
    return transport_context(job, transport_class=CleanupTransport, capability="tap_link_cleanup", allow_write=allow_write)


def create_scan(market, account, request_key, *, store=None, source_scope='all'):
    if market not in {"mx", "br", "it", "uk", "us", "jp", "de"} or not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_key):
        raise ValueError("taplink_scope_invalid")
    account_for(market, account, check_maintenance=False)
    if source_scope not in {'all','campaign','selected'}:raise ValueError('taplink_scope_invalid')
    repository = store or CleanupStore()
    key = hashlib.sha256(request_key.encode()).hexdigest()
    with repository.lock():
        for old in repository.list(market):
            if old["request_hash"] == key:
                if old["account"] != account or old.get('source_scope','all')!=source_scope:
                    raise ValueError("taplink_request_key_conflict")
                return old
        job = {"job_id": "tc_" + uuid4().hex, "market": market, "account": account, "request_hash": key,
               "created_at": now(), "state": "draft", "items": [], "scopes_done": 0, "scopes_total": 0, "scan_complete": False, "error": "",'source_scope':source_scope}
        repository.save(job)
        return job


def launch(job_id, action, *, selected=None, store=None):
    repository = store or CleanupStore()
    with repository.lock():
        job = repository.read(job_id)
        if action == 'resume_scan':
            if job['state']!='paused' or job.get('action')!='scan' or job.get('scan_complete') or any(i.get('delete_attempted') for i in job['items']):
                raise ValueError('taplink_cleanup_verify_required')
            job.update(resume_read=True,scopes_done=0)
            (repository.root/f'{job_id}.stop').unlink(missing_ok=True)
            action='scan'
        elif action == "delete":
            require_capability(job["market"], "tap_link_cleanup")
            if not job["scan_complete"] or job["state"] != "scanned" or not isinstance(selected, list) or not 1 <= len(selected) <= 500 or len(set(selected)) != len(selected):
                raise ValueError("taplink_cleanup_selection_required")
            eligible = {i["list_id"] for i in job["items"] if i.get("eligible") and i["state"] == "invalid" and not i.get("delete_attempted")}
            if not set(selected) <= eligible:
                raise ValueError("taplink_cleanup_selection_invalid")
            job["selected"] = selected
            (repository.root / f"{job_id}.stop").unlink(missing_ok=True)
        elif action in {"scan", "clean"}:
            if action == "clean":
                require_capability(job["market"], "tap_link_cleanup")
            if any(i.get("delete_attempted") for i in job["items"]):
                raise ValueError("taplink_cleanup_verify_required")
            job.update(items=[], scopes_done=0, scan_complete=False,resume_read=False)
            (repository.root / f"{job_id}.stop").unlink(missing_ok=True)
        elif action != "verify":
            raise ValueError("taplink_action_invalid")
        job.update(state="queued", action=action, error="")
        repository.save(job)
        try:
            process = subprocess.Popen([str(config.ROOT / ".venv/bin/python"), "-m", "bdhub.send.taplink.cleanup_worker", "--job-id", job_id],
                                       cwd=config.ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            job["worker_pid"] = process.pid
            repository.save(job)
        except OSError:
            job.update(state="paused", error="taplink_worker_start_failed")
            repository.save(job)
            raise ValueError("taplink_worker_start_failed") from None
    return job


def _verify_deleted(job, transport, store):
    grouped = {}
    for item in job["items"]:
        if item.get("delete_attempted") and item["state"] != "deleted":
            grouped.setdefault((item["source"], item["campaign_id"]), []).append(item)
    for (source, campaign), items in grouped.items():
        existing = {r["list_id"] for r in transport.lists_in_scope(source, campaign)}
        for item in items:
            item.update(state="deleted" if item["list_id"] not in existing else "delete_unknown", eligible=False,
                        reason="平台回查确认已删除" if item["list_id"] not in existing else "删除结果待核验，不自动重试")
            store.block(job["market"], item["list_id"], item["state"])
        store.save(job)


def scan_inventory(job, transport, repository, check_stop, links=None):
    campaigns = transport.campaign_catalog()
    known = {j["offer"]["campaign_id"] for j in (links or TapLinkStore()).list(job["market"]) if j["account"] == job["account"] and j["route"] == "campaign"}
    scopes = [(2, "0"), *((1, cid) for cid in sorted(set(campaigns) | known))]
    scope=job.get('source_scope','all')
    scopes=[s for s in scopes if scope=='all' or s[0]==(1 if scope=='campaign' else 2)]
    job["scopes_total"] = len(scopes)
    repository.save(job)
    seen = set()
    positions={r['list_id']:idx for idx,r in enumerate(job['items'])}
    job['reused_checks']=0
    for source, campaign in scopes:
        check_stop()
        rows = transport.lists_in_scope(source, campaign)
        for row in rows:
            if row["list_id"] in seen:
                raise ValueError("taplink_cleanup_duplicate_list")
            seen.add(row["list_id"])
            check_stop()
            prior=job['items'][positions[row['list_id']]] if row['list_id'] in positions else None
            if job.get('resume_read') and reusable_check(prior,row,campaigns) and not repository.retirement_state(job['market'],row['list_id']):
                job['reused_checks']+=1
                continue
            try:
                row.update(classify_list(row, transport.products(row), campaigns=campaigns))
            except ValueError as exc:
                if str(exc) in {"taplink_remote_read_failed", "taplink_cleanup_stopped"}:
                    raise
                row.update(state="unknown", eligible=False, reason="商品详情未能完整核验，暂不删除", products=[])
            if repository.retirement_state(job["market"], row["list_id"]):
                row.update(state="unknown", eligible=False, reason="已有删除记录，请核验原清洗任务，不重复删除")
            row["observed_at"] = now()
            if row['list_id'] in positions:
                job['items'][positions[row['list_id']]]=row
            else:
                positions[row['list_id']]=len(job['items'])
                job["items"].append(row)
            # 扫描只有读请求，按小批保存进度，避免万条历史逐项重写整个文件。
            if len(job["items"]) % 20 == 0:
                repository.save(job)
        job["scopes_done"] += 1
        repository.save(job)
    job.update(state="scanned", scan_complete=True,items=[r for r in job['items'] if r['list_id'] in seen])


def reusable_check(prior,current,campaigns):
    """仅续查复用一小时内的已完成检查；保留原观测时间，删除时仍即时复查。"""
    if not prior or prior.get('state') not in {'valid','invalid','mixed'}:
        return False
    try:
        age=datetime.now(timezone.utc)-datetime.fromisoformat(prior['observed_at'])
        if not timedelta(0)<=age<=timedelta(hours=1):return False
    except (KeyError,TypeError,ValueError):return False
    if current.get('platform_updated_at') is None or any(prior.get(k)!=current.get(k) for k in ('list_id','name','url','source','campaign_id','product_total','platform_updated_at')):
        return False
    return not any(campaign_time_status(campaigns[p['campaign_id']])[0]=='EXPIRED' for p in prior.get('products',[]) if p.get('campaign_id') in campaigns)


def delete_selected(job, transport, repository, check_stop):
    campaigns = transport.campaign_catalog()
    scopes = {(i["source"], i["campaign_id"]) for i in job["items"] if i["list_id"] in job["selected"]}
    current_scopes = {(source, cid): {r["list_id"]: r for r in transport.lists_in_scope(source, cid)} for source, cid in scopes}
    for item in job["items"]:
        if item["list_id"] not in job["selected"]:
            continue
        check_stop()
        current = current_scopes[(item["source"], item["campaign_id"])].get(item["list_id"])
        if current is None:
            item.update(state="deleted", eligible=False, reason="平台列表中已不存在")
            repository.block(job["market"], item["list_id"], "deleted")
            repository.save(job)
            continue
        if repository.retirement_state(job["market"], item["list_id"]):
            item.update(state="unknown", eligible=False, reason="已有删除记录，请核验原清洗任务，不重复删除")
            repository.save(job)
            continue
        health = classify_list(current, transport.products(current), campaigns=campaigns)
        if not health["eligible"]:
            item.update(health)
            repository.save(job)
            continue
        if item.get("delete_attempted"):
            raise ValueError("taplink_cleanup_verify_required")
        item.update(health)
        item.update(state="deleting", delete_attempted=True, delete_started_at=now(), eligible=False)
        repository.save(job)
        repository.block(job["market"], item["list_id"], "deleting")
        result = transport.delete_list(item["list_id"])
        item.update(state="delete_unknown", platform_code=result.code if isinstance(result.code, (str, int)) else None)
        repository.save(job)
        if result.ambiguous or result.http_status != 200 or result.code != 0:
            break
    _verify_deleted(job, transport, repository)
    job["state"] = "needs_verification" if any(i["state"] == "delete_unknown" for i in job["items"]) else "scanned"


def run(job_id, *, store=None, factory=cleanup_transport, links=None):
    repository = store or CleanupStore()
    with repository.lock():
        job = repository.read(job_id)
        if job["state"] != "queued":
            return job
        action = job["action"]
        job["state"] = "scanning" if action in {"scan", "clean"} else "deleting" if action == "delete" else "verifying"
        repository.save(job)
        def check_stop():
            if action != "verify" and (repository.root / f"{job_id}.stop").exists():
                raise ValueError("taplink_cleanup_stopped")
        try:
            with factory(job, allow_write=action in {"delete", "clean"}) as transport:
                transport.check_stop = check_stop
                if action in {"scan", "clean"}:
                    scan_inventory(job, transport, repository, check_stop, links)
                    if action == "clean":
                        job["selected"] = [i["list_id"] for i in job["items"] if i.get("eligible") and i["state"] == "invalid"]
                        job["state"] = "deleting"
                        repository.save(job)
                        delete_selected(job, transport, repository, check_stop)
                elif action == "delete":
                    delete_selected(job, transport, repository, check_stop)
                else:
                    _verify_deleted(job, transport, repository)
                    job["state"] = "needs_verification" if any(i["state"] == "delete_unknown" for i in job["items"]) else "scanned" if job["scan_complete"] else "paused"
        except Exception as exc:
            job.update(state="needs_verification" if any(i.get("delete_attempted") and i["state"] != "deleted" for i in job["items"]) else "paused",
                       error=str(exc) if isinstance(exc, ValueError) and str(exc).startswith("taplink_") else "taplink_cleanup_interrupted")
        repository.save(job)
        return job


def public_scan(job):
    result = {k: v for k, v in job.items() if k not in {"worker_pid", "request_hash"}}
    result["counts"] = {"total": len(job["items"]), **{state: sum(i["state"] == state for i in job["items"]) for state in ("invalid", "valid", "mixed", "unknown", "deleted", "delete_unknown")}}
    if job["state"] in {"queued", "scanning", "deleting", "verifying"} and (datetime.now(timezone.utc) - datetime.fromisoformat(job["updated_at"])).total_seconds() > 20:
        probe = subprocess.run(["ps", "-p", str(int(job.get("worker_pid") or 0)), "-o", "command="], capture_output=True, text=True, timeout=3)
        if probe.returncode or "bdhub.send.taplink.cleanup_worker" not in probe.stdout or job["job_id"] not in probe.stdout:
            result.update(state="needs_verification" if any(i.get("delete_attempted") for i in job["items"]) else "paused", error="taplink_cleanup_interrupted")
    return result


def selected_library(market, account, *, store=None, links=None, source_scope='all'):
    repository, link_store = store or CleanupStore(), links or TapLinkStore()
    rows, seen = [], set()
    latest = None
    if source_scope not in {'all','campaign','selected'}:raise ValueError('taplink_scope_invalid')
    complete_times = {}
    for scan in repository.list(market):
        if scan["account"] != account:
            continue
        scan_scope=scan.get('source_scope','all')
        scope_types={1,2} if scan_scope=='all' else {1 if scan_scope=='campaign' else 2}
        requested_types={1,2} if source_scope=='all' else {1 if source_scope=='campaign' else 2}
        if not scope_types & requested_types:continue
        latest = latest or public_scan(scan)
        for row in scan.get("items", []):
            if row['source'] not in requested_types or row['source'] in complete_times:continue
            if row["list_id"] in seen:
                continue
            seen.add(row["list_id"])
            status = repository.retirement_state(market, row["list_id"])
            if row["state"] == "deleted" or status == "deleted":
                continue
            rows.append({**row, "row_key": row["list_id"], "state": status or row["state"]})
        if scan.get("scan_complete"):
            for kind in scope_types:complete_times.setdefault(kind,scan['updated_at'])
    urls = {r.get("url") for r in rows if r.get("url")}
    for child in link_store.list(market):
        if child["account"] != account or not child.get("receipt", {}).get("url"):
            continue
        lid = (child.get("card") or {}).get("list_id") or child["receipt"].get("list_id")
        kind=1 if child['route']=='campaign' else 2
        if source_scope!='all' and kind!=(1 if source_scope=='campaign' else 2):continue
        if (lid and (lid in seen or repository.retirement_state(market, lid) == "deleted")) or child["receipt"]["url"] in urls:
            continue
        if lid and complete_times.get(kind) and child["created_at"] <= complete_times[kind]:
            continue
        status = repository.retirement_state(market, lid) if lid else ""
        rows.append({"row_key": lid or child["job_id"], "list_id": lid or "", "name": child["list_name"], "url": child["receipt"]["url"],
                     "source": 1 if child["route"] == "campaign" else 2, "campaign_id": child["offer"]["campaign_id"], "product_total": 1,
                     "state": status or ("valid" if child["state"] in {"ready", "card_verified"} else "unknown"), "reason": "删除结果待核验" if status else "BDHub 已创建", "eligible": False,
                     "products": [{"pid": child["offer"]["pid"], "name": child["offer"]["product_name"], "reason": "已创建"}]})
        if lid:
            seen.add(lid)
        urls.add(child["receipt"]["url"])
    return rows, latest
