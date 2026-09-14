"""复用 Campaign 查询与账号身份；TapLink 写请求仅执行一次。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import requests

from bdhub import config, scheduled_relogin
from bdhub.account_policy import resolve_account_policy
from bdhub.enrich.identity_store import load_identity
from bdhub.enrich.profile_lease import ProfileLease
from bdhub.hub.markets import identity_for, require_capability
from bdhub.imbase.account_binding import sender_accounts, resolve_bound_profile
from bdhub.send.sharelink.transport import (
    BrowserResult, ShareLinkBrowserTransport, SEARCH_PATH, OPPORTUNITY_SEARCH_PATH,
    OPPORTUNITY_DETAIL_PATH, PICK_UP_SELECT_PATH, JOINED_CAMPAIGN_LIST_PATH, JOINED_CAMPAIGN_PRODUCT_PATH,
)
from .protocol import CREATE_PATH, CARD_LIST_PATH, create_payload, creation_receipt, find_card


class TapLinkError(ValueError):
    pass


def account_for(market: str, name: str, *, check_maintenance=True):
    cfg = config.load()
    account = next((a for a in sender_accounts(config.load_accounts(cfg), market)
                    if a.name == name and not resolve_account_policy(a).listener_pool), None)
    if account is None:
        raise TapLinkError("taplink_sender_account_unavailable")
    if check_maintenance and scheduled_relogin.maintenance_due(account, initialize=False, ignore_retry_throttle=True):
        raise TapLinkError("taplink_account_maintenance_due")
    return cfg, account


class TapLinkTransport(ShareLinkBrowserTransport):
    READ_ENDPOINTS = {(SEARCH_PATH, "POST"), (OPPORTUNITY_SEARCH_PATH, "POST"), (OPPORTUNITY_DETAIL_PATH, "GET"),
                      (JOINED_CAMPAIGN_LIST_PATH, "GET"), (JOINED_CAMPAIGN_PRODUCT_PATH, "GET"), (CARD_LIST_PATH, "GET")}
    WRITE_ENDPOINTS = {(CREATE_PATH, "POST")}
    def __init__(self, identity, account, *, allow_write=False):
        super().__init__(page=SimpleNamespace(on=lambda *_: None), identity=identity,
                         fp=account.fp, device_id=account.device_id, min_interval_seconds=1)
        bundle = load_identity(account.headers_json)
        self.headers = {k: v for k, v in bundle.headers.items() if not k.startswith(":")
                        and k.lower() not in {"host", "content-length", "origin", "referer"}}
        self.headers.update(origin="https://partner.tiktokshop.com", referer="https://partner.tiktokshop.com/")
        if identity.market != "mx":
            from urllib.parse import urlsplit
            portal = urlsplit(identity.home)
            origin = f"{portal.scheme}://{portal.netloc}"
            self.headers.update(origin=origin, referer=origin + "/")
        self.session = requests.Session()
        self.session.trust_env = False
        self.allow_write = allow_write

    def _xhr(self, *, method, path, params, payload, write):
        reads = self.READ_ENDPOINTS
        writes = self.WRITE_ENDPOINTS
        if (write and (not self.allow_write or (path, method) not in writes)) or (not write and (path, method) not in reads):
            raise TapLinkError("taplink_endpoint_not_allowed")
        self._pace()
        started = datetime.now(timezone.utc)
        try:
            response = self.session.request(method, self.identity.host + path, params=params,
                                            json=payload, headers=self.headers, timeout=(5, 25), allow_redirects=False)
        except requests.RequestException:
            return BrowserResult("network_error", 0, {}, True, write, request_dispatched_at=started)
        try:
            body = response.json()
        except ValueError:
            body = {"code": "non_json"}
        return BrowserResult("response", response.status_code, body if isinstance(body, dict) else {"code": "malformed"},
                             True, False, has_turing=bool(response.headers.get("bdturing-verify")),
                             system_error_3=response.headers.get("x-tt-system-error") == "3",
                             request_dispatched_at=started, finished_at=datetime.now(timezone.utc))

    @staticmethod
    def require_read(result):
        if (result.http_status != 200 or type(result.code) is not int or result.code != 0
                or result.ambiguous or result.has_turing or result.system_error_3):
            raise TapLinkError("taplink_remote_read_failed")
        return result.payload

    def offer(self, job):
        """只查询固定 PID/Campaign，不在创建前换成另一个最优活动。"""
        pid, campaign = job["offer"]["pid"], job["offer"]["campaign_id"]
        if job["route"] == "campaign":
            found = self.revalidate_joined_campaign(pid=pid, campaign_id=campaign)
            if found["status"] != "found":
                raise TapLinkError("taplink_campaign_product_unavailable")
            return self.exact(found.get("items", []), pid, campaign), False
        found = self._search_selected_pid(pid)
        if found["status"] == "failed":
            raise TapLinkError("taplink_selected_read_failed")
        exact = self.exact(found.get("items", []), pid, campaign, required=False)
        if exact:
            return self.sample_facts(job, exact), False
        raise TapLinkError("taplink_selected_source_required")

    def sample_facts(self, job, raw):
        from bdhub.research.campaign_catalog import sample_quantity
        product = raw.get("campaign_product") or {}
        if (job["purpose"] != "first" and not (job.get("filter_rules") or {}).get("samples_only")) or (sample_quantity(product.get("sample_quota")) or 0) > 0:
            return raw
        # 已选/高机会可能不返回额度。仅一发需要同活动的实时样品证据。
        found = self.revalidate_joined_campaign(pid=job["offer"]["pid"], campaign_id=job["offer"]["campaign_id"])
        if found["status"] != "found":
            raise TapLinkError("taplink_sample_evidence_missing")
        joined = self.exact(found.get("items", []), job["offer"]["pid"], job["offer"]["campaign_id"])
        return {**raw, "campaign_product": {**product, "sample_quota": joined["campaign_product"].get("sample_quota")},
                "_sample_source": "live_joined_campaign"}

    @staticmethod
    def exact(items, pid, campaign, *, required=True):
        matches = [r for r in items if str((r.get("campaign_product") or {}).get("product_id")) == pid
                   and str((r.get("campaign_info") or {}).get("campaign_id")) == campaign]
        if len(matches) == 1:
            return matches[0]
        if required or matches:
            raise TapLinkError("taplink_fixed_campaign_missing")
        return None

    def selected_offer(self, job):
        found = self._search_selected_pid(job["offer"]["pid"])
        if found["status"] == "failed":
            raise TapLinkError("taplink_selected_read_failed")
        return self.sample_facts(job, self.exact(found.get("items", []), job["offer"]["pid"], job["offer"]["campaign_id"]))

    def create(self, job):
        payload = create_payload(pid=job["offer"]["pid"], campaign_id=job["offer"]["campaign_id"],
                                 creator_pct=job["creator_commission"], name=job["list_name"], route=job["route"])
        result = self._xhr(method="POST", path=CREATE_PATH, params=self._params(), payload=payload, write=True)
        if (result.ambiguous or result.http_status != 200 or result.has_turing or result.system_error_3
                or result.code in {None, "non_json", "malformed", 98001001}):
            raise TapLinkError("taplink_create_result_unknown")
        if type(result.code) is not int or result.code != 0:
            raise TapLinkError("taplink_create_rejected")
        return creation_receipt(result.payload)

    def card(self, job):
        matches = {}
        for page in range(1, 21):
            result = self._xhr(method="GET", path=CARD_LIST_PATH, params=self._params() | {
                "cur_page": page, "page_size": 20, "version": 1, "search_type": 2, "key_word": job["offer"]["pid"]}, payload=None, write=False)
            body = self.require_read(result)
            card = find_card(body, pid=job["offer"]["pid"], campaign_id=job["offer"]["campaign_id"],
                             route=job["route"], name=job["list_name"], list_id=job.get("receipt", {}).get("list_id"), strict=job.get("unified", False))
            if card:
                matches[card["list_id"]] = card
                if job.get("receipt", {}).get("list_id"):
                    return card
            if len(body["data"]["list"]) < 20:
                if len(matches) > 1:
                    raise TapLinkError("taplink_card_binding_not_unique")
                return next(iter(matches.values()), None)
        raise TapLinkError("taplink_card_page_limit")

    def find_existing(self, pid):
        """按精确 PID 检查平台已有列表；存在即跳过创建，不依赖新规则或命名。"""
        from .cleanup import CleanupStore
        for page in range(1, 21):
            result = self._xhr(method="GET", path=CARD_LIST_PATH, params=self._params() | {
                "cur_page": page, "page_size": 20, "version": 1, "search_type": 2, "key_word": pid}, payload=None, write=False)
            body = self.require_read(result)
            data = body.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise TapLinkError("taplink_existing_lookup_incomplete")
            for row in data["list"]:
                if not isinstance(row, dict):
                    raise TapLinkError("taplink_existing_lookup_incomplete")
                products = row.get("campaign_products", [])
                if not isinstance(products, list):
                    raise TapLinkError("taplink_existing_lookup_incomplete")
                if any(isinstance(p, dict) and str(p.get("product_id") or "") == pid for p in products):
                    lid = str(row.get("product_list_id") or "")
                    if not lid.isascii() or not lid.isdigit() or int(lid) <= 0:
                        raise TapLinkError("taplink_existing_lookup_incomplete")
                    if CleanupStore().retirement_state(self.identity.market, lid) != "deleted":
                        return {"list_id": lid, "name": str(row.get("product_list_name") or ""), "reason": "平台已有该 PID 的 TapLink，跳过重复创建"}
            if len(data["list"]) < 20:
                return None
        raise TapLinkError("taplink_existing_lookup_incomplete")


@contextmanager
def transport_context(job, *, transport_class=TapLinkTransport, capability="tap_link", allow_write=False):
    if allow_write:
        require_capability(job["market"], capability)
    cfg, account = account_for(job["market"], job["account"])
    identity = identity_for(job["market"], account=account, cfg=cfg).require_product_search()
    if not identity.partner_id_is_own:
        raise TapLinkError("taplink_identity_not_own")
    with ProfileLease(resolve_bound_profile(account), account=account.name, market=job["market"], operation="taplink_prepare"):
        transport = transport_class(identity, account, allow_write=allow_write)
        try:
            yield transport
        finally:
            transport.session.close()


def transport_for(job, *, allow_write=False):
    return transport_context(job, allow_write=allow_write)
