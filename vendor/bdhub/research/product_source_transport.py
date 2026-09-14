"""商品源读取、选入商品与加入 Seller Campaign；不提供建链接口。"""
from decimal import Decimal
import re

from bdhub.send.sharelink.candidates import choose_best_opportunity_campaign
from bdhub.send.sharelink.transport import JOINED_CAMPAIGN_LIST_PATH, OPPORTUNITY_DETAIL_PATH, PICK_UP_SELECT_PATH
from bdhub.send.taplink.transport import TapLinkTransport, transport_context

SELLER_JOIN_PATH = "/api/v1/affiliate/partner/campaign/seller_requested/review"


class SourceUnavailable(ValueError):
    """明确的逐项不可用，可跳过并继续后续 PID。"""


def seller_join_payload(campaign_id, email):
    if not re.fullmatch(r"[0-9]{10,32}", str(campaign_id)) or not isinstance(email, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
        raise ValueError("source_contact_email_required")
    return {"campaign_id": campaign_id, "is_joined": True,
            "contact_info": {"campaign_contact_info": [{"type": 2, "value": email}]}}


class SourceTransport(TapLinkTransport):
    WRITE_ENDPOINTS = {(PICK_UP_SELECT_PATH, "POST"), (SELLER_JOIN_PATH, "POST")}

    def _xhr(self, **kwargs):
        if getattr(self, "check_stop", None):
            self.check_stop()
        return super()._xhr(**kwargs)

    def opportunity(self, pid):
        search = self._search_opportunity_pid(pid)
        if search["status"] == "not_found":
            raise SourceUnavailable("高机会商品中未搜索到该 PID")
        if search["status"] != "found":
            raise ValueError("source_opportunity_read_failed")
        result = self._xhr(method="GET", path=OPPORTUNITY_DETAIL_PATH, params=self._params() | {"product_id": pid}, payload=None, write=False)
        body = self.require_read(result)
        candidates = (body.get("data") or {}).get("product_campaign_detail")
        if not isinstance(candidates, list):
            raise ValueError("source_campaign_detail_malformed")
        candidate = choose_best_opportunity_campaign(candidates)
        if not candidate:
            raise SourceUnavailable("没有可选的有效 Campaign")
        raw = self._opportunity_as_pickup_item(pid=pid, product=search["product"], campaign_item=candidate)
        product = raw["campaign_product"]
        if product.get("is_under_governed") is True or str(product.get("unavailable_type") or "0") not in {"0", "0.0"}:
            raise SourceUnavailable("平台标记商品不可选")
        if Decimal(str(candidate["campaign"].get("commission") or 0)) <= 0:
            raise SourceUnavailable("活动佣金无效")
        return raw

    def selected(self, pid, campaign_id):
        result = self._search_selected_pid(pid)
        if result["status"] == "failed":
            raise ValueError("source_selected_read_failed")
        return self.exact(result.get("items", []), pid, campaign_id, required=False)

    def select_product(self, pid, campaign_id):
        return self._xhr(method="POST", path=PICK_UP_SELECT_PATH, params=self._params(),
                         payload={"product_id": pid, "campaign_id": campaign_id}, write=True)

    def campaigns(self, filters):
        rows, seen = [], set()
        total = None
        for page in range(1, 21):
            result = self._xhr(method="GET", path=JOINED_CAMPAIGN_LIST_PATH,
                               params=self._params() | filters | {"cur_page": page, "page_size": 100}, payload=None, write=False)
            data = self.require_read(result).get("data")
            if not isinstance(data, dict):
                raise ValueError("source_campaign_list_malformed")
            current_total = int(data.get("total_num", -1))
            if current_total < 0 or (total is not None and total != current_total):
                raise ValueError("source_campaign_list_changed")
            total = current_total
            batch = data.get("campaign", []) if current_total == 0 else data.get("campaign")
            if not isinstance(batch, list):
                raise ValueError("source_campaign_list_malformed")
            for row in batch:
                cid = str(row.get("campaign_id") or "")
                if not re.fullmatch(r"[0-9]{10,32}", cid) or cid in seen:
                    raise ValueError("source_campaign_list_incomplete")
                rows.append(row)
                seen.add(cid)
            if len(rows) == total:
                return rows
            if len(batch) < 100 or len(rows) > total:
                raise ValueError("source_campaign_list_incomplete")
        raise ValueError("source_campaign_page_limit")

    def seller_campaigns(self):
        return self.campaigns({"status": 0, "crs_campaign_type": 4, "campaign_list_scene": 2,
                               "seller_campaign_type": 0, "sort_field": 0})

    def joined_ids(self):
        return {str(c["campaign_id"]) for c in self.campaigns({"campaign_join_status_category": "1",
                "crs_campaign_types": ""})}

    def join_seller(self, campaign_id, email):
        return self._xhr(method="POST", path=SELLER_JOIN_PATH, params=self._params(),
                         payload=seller_join_payload(campaign_id, email), write=True)


def source_transport(job, *, allow_write=False):
    return transport_context(job, transport_class=SourceTransport, allow_write=allow_write,
                             capability="product_select" if job["kind"] == "manual_pids" else "campaign_join")
