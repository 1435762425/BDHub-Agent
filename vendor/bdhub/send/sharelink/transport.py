"""账号绑定浏览器中的 MX Campaign 搜索与 ShareLink 创建。"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

from .candidates import choose_best_opportunity_campaign, int_value, text_value
from .joined_campaign_cache import (
    JoinedCampaignCacheError,
    default_cache_path,
    load_joined_campaign_cache,
)


SEARCH_PATH = "/api/v1/affiliate/partner/product/pick_up/list"
OPPORTUNITY_SEARCH_PATH = "/api/v1/affiliate/partner/product/opportunity_product/list"
OPPORTUNITY_DETAIL_PATH = "/api/v1/affiliate/partner/product/opportunity_product/campaign_detail"
PICK_UP_SELECT_PATH = "/api/v1/affiliate/partner/product/pick_up/select"
JOINED_CAMPAIGN_LIST_PATH = "/api/v1/affiliate/partner/campaign/list"
JOINED_CAMPAIGN_PRODUCT_PATH = "/api/v1/affiliate/partner/campaign/product/list"
LINK_PATH = "/api/v1/affiliate/partner/campaign/product/link"

JOINED_CAMPAIGN_PAGE_PATHS = {
    1: "/affiliate-campaign/partner-collabs/agency/detail",
    5: "/affiliate-campaign/partner-collabs/seller/joined-detail",
    7: "/affiliate-campaign/partner-collabs/exclusive/detail",
}
JOINED_CAMPAIGN_COLLABORATION_TYPES = {
    "1": "My collabs",
    "5": "Seller collabs",
    "7": "Exclusive TikTok Shop collabs",
}


_XHR_SCRIPT = r"""
({method, url, payload, timeoutMs}) => new Promise((resolve) => {
  const xhr = new XMLHttpRequest();
  xhr.open(method, url, true);
  xhr.withCredentials = true;
  xhr.timeout = timeoutMs;
  xhr.setRequestHeader('accept', 'application/json, text/plain, */*');
  if (payload !== null) xhr.setRequestHeader('content-type', 'application/json');
  const done = (kind) => resolve({
    kind,
    http_status: Number(xhr.status || 0),
    text: String(xhr.responseText || ''),
    has_turing: Boolean(xhr.getResponseHeader('bdturing-verify')),
    system_error_3: String(xhr.getResponseHeader('x-tt-system-error') || '') === '3'
  });
  xhr.onload = () => done('response');
  xhr.onerror = () => done('network_error');
  xhr.ontimeout = () => done('timeout');
  xhr.send(payload === null ? null : JSON.stringify(payload));
})
"""


_VERIFY_SCRIPT = r"""
() => {
  const visible = (node) => {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    const box = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0;
  };
  const selectors = [
    'iframe[src*="captcha" i]', '[id*="captcha" i]', '[class*="captcha" i]',
    '[id*="slider" i]', '[class*="slider" i][class*="verify" i]',
    '[class*="challenge" i][class*="verify" i]'
  ];
  if (selectors.some(selector => Array.from(document.querySelectorAll(selector)).some(visible))) return true;
  const text = String(document.body?.innerText || '').toLowerCase();
  return ['slide to verify','complete the verification','security verification',
    '拖动滑块','滑块验证','请完成验证','安全验证','验证码'].some(marker => text.includes(marker));
}
"""


@dataclass(frozen=True, slots=True)
class BrowserResult:
    kind: str
    http_status: int
    payload: dict[str, Any]
    dispatched: bool
    ambiguous: bool
    has_turing: bool = False
    system_error_3: bool = False
    has_x_bogus: bool = False
    has_signature: bool = False
    request_dispatched_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def code(self) -> object:
        return self.payload.get("code")

    def transport_snapshot(self) -> dict[str, bool]:
        return {
            "has_turing": self.has_turing,
            "system_error_3": self.system_error_3,
            "runtime_added_x_bogus": self.has_x_bogus,
            "runtime_added_signature": self.has_signature,
        }


def _auth_error(result: BrowserResult) -> bool:
    code = result.payload.get("code")
    if result.http_status in {401, 403} or code in {10000, 401, 403, 16201010}:
        return True
    text = json.dumps(result.payload, ensure_ascii=False).casefold()[:1600]
    return any(marker in text for marker in (
        "captcha", "verification", "signature", "x-bogus", "unauthorized",
        "forbidden", "session expired", "invalid session", "login",
    ))


def _transient_error(result: BrowserResult) -> bool:
    code = result.payload.get("code")
    return (
        result.http_status in {0, 429, 500, 502, 503, 504}
        or code in {429, 100000, 98001001}
        or result.system_error_3
    )


def _clean_success(result: BrowserResult) -> bool:
    return (
        result.http_status == 200
        and result.payload.get("code") == 0
        and not result.has_turing
        and not result.system_error_3
    )


def _select_success(result: BrowserResult) -> bool:
    if _clean_success(result):
        return True
    message = str(
        result.payload.get("msg") or result.payload.get("message") or ""
    ).casefold()
    return (
        ("already" in message and ("select" in message or "selected" in message))
        or "duplicate" in message
        or "repeated" in message
    )


class ShareLinkBrowserTransport:
    def __init__(self, *, page, identity, min_interval_seconds: float = 1.0,
                 timeout_seconds: int = 40, fp: str = "",
                 device_id: str = "0",
                 joined_campaign_cache_ttl_seconds: int = 21_600,
                 joined_campaign_cache_path: str | Path | None = None,
                 allow_live_joined_campaign_refresh: bool = False) -> None:
        self.page = page
        self.identity = identity
        self.min_interval_seconds = max(1.0, float(min_interval_seconds))
        self.timeout_ms = max(5_000, int(timeout_seconds * 1000))
        self.fp = str(fp or "").strip()
        self.device_id = str(device_id or "0").strip() or "0"
        self._last_request_at = 0.0
        self._request_meta: dict[str, bool] = {}
        self.joined_campaign_cache_ttl_seconds = max(
            60, int(joined_campaign_cache_ttl_seconds),
        )
        self.joined_campaign_cache_path = (
            Path(joined_campaign_cache_path)
            if joined_campaign_cache_path is not None
            else default_cache_path(self.identity.im_market_partner_id)
        )
        self.allow_live_joined_campaign_refresh = bool(
            allow_live_joined_campaign_refresh
        )
        self._joined_campaigns: list[dict] = []
        self._joined_campaign_index: dict[str, list[dict]] = {}
        self._joined_campaign_indexed_at = 0.0
        self._joined_campaign_cache_expires_at = 0.0
        self._joined_campaign_cache_ready = False
        self._joined_page_context: tuple[str, str, int] | None = None
        self._joined_page_probe_error = ""
        self.page.on("request", self._observe_request)

    def _observe_request(self, request) -> None:
        url = str(request.url or "")
        observed_paths = (
            SEARCH_PATH,
            OPPORTUNITY_SEARCH_PATH,
            OPPORTUNITY_DETAIL_PATH,
            PICK_UP_SELECT_PATH,
            JOINED_CAMPAIGN_LIST_PATH,
            JOINED_CAMPAIGN_PRODUCT_PATH,
            LINK_PATH,
        )
        if not any(path in url for path in observed_paths):
            return
        lowered = url.casefold()
        self._request_meta = {
            "has_x_bogus": "x-bogus=" in lowered,
            "has_signature": "_signature=" in url,
        }

    def _pace(self) -> None:
        wait = self.min_interval_seconds - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _params(self) -> dict[str, str]:
        params = {
            "user_language": str(self.identity.user_language or "zh-CN"),
            "partner_id": str(self.identity.im_market_partner_id),
            "aid": str(self.identity.aid),
            "app_name": "i18n_ecom_alliance",
            "device_id": self.device_id,
            "device_platform": "web",
            "cookie_enabled": "true",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Mozilla",
            "browser_version": "5.0",
            "browser_online": "true",
            "timezone_name": "Asia/Shanghai",
        }
        if self.fp:
            params["fp"] = self.fp
        return params

    def _human_verification(self) -> bool:
        try:
            return self.page.evaluate(_VERIFY_SCRIPT) is True
        except Exception:
            return False

    def _xhr(self, *, method: str, path: str, params: dict[str, object],
             payload: dict | None, write: bool) -> BrowserResult:
        result = self._xhr_request(method=method, path=path, params=params, payload=payload, write=write)
        observer = getattr(self, "result_observer", None)
        if observer is not None:
            try:
                observer(result)
            except Exception:
                # 观测落盘失败不改变真实平台结果，更不能触发第二次写入。
                import logging
                logging.getLogger(__name__).warning("ShareLink能力观测保存失败")
        return result

    def _xhr_request(self, *, method: str, path: str, params: dict[str, object],
                     payload: dict | None, write: bool) -> BrowserResult:
        if self._human_verification():
            return BrowserResult(
                kind="verification_required",
                http_status=0,
                payload={"code": "verification_required"},
                dispatched=False,
                ambiguous=False,
            )
        query = urlencode([(str(key), str(value)) for key, value in params.items()])
        url = f"{self.identity.host}{path}?{query}"
        self._pace()
        self._request_meta = {}
        dispatched_at = datetime.now(timezone.utc)
        try:
            raw = self.page.evaluate(_XHR_SCRIPT, {
                "method": method.upper(),
                "url": url,
                "payload": payload,
                "timeoutMs": self.timeout_ms,
            }) or {}
        except Exception:
            finished_at = datetime.now(timezone.utc)
            return BrowserResult(
                kind="result_unknown" if write else "transport_error",
                http_status=0,
                payload={"code": "browser_context_interrupted"},
                dispatched=True,
                ambiguous=write,
                request_dispatched_at=dispatched_at,
                finished_at=finished_at,
                **self._request_meta,
            )
        finished_at = datetime.now(timezone.utc)
        text = str(raw.get("text") or "")
        try:
            result = json.loads(text) if text else {}
        except (TypeError, ValueError):
            result = {"code": "non_json"}
        raw_kind = str(raw.get("kind") or "")
        ambiguous = write and raw_kind in {"timeout", "network_error"}
        return BrowserResult(
            kind=raw_kind or "response",
            http_status=int(raw.get("http_status") or 0),
            payload=result if isinstance(result, dict) else {"code": "malformed"},
            dispatched=True,
            ambiguous=ambiguous,
            has_turing=bool(raw.get("has_turing")),
            system_error_3=bool(raw.get("system_error_3")),
            request_dispatched_at=dispatched_at,
            finished_at=finished_at,
            **self._request_meta,
        )

    def _search_selected_pid(self, pid: str, *, page_size: int = 10,
                             max_pages: int = 20) -> dict:
        collected: list[dict] = []
        seen: set[tuple[str, str]] = set()
        total_num = 0
        empty_confirmations = 0
        for page_no in range(1, max_pages + 1):
            payload = {
                "product_ids": [str(pid)],
                "page_size": int(page_size),
                "cur_page": page_no,
                "filter": {
                    "product_source": [],
                    "campaign_type": [],
                    "label_type": [],
                    "product_status": 1,
                },
            }
            attempts = 0
            while True:
                attempts += 1
                result = self._xhr(
                    method="POST",
                    path=SEARCH_PATH,
                    params=self._params(),
                    payload=payload,
                    write=False,
                )
                if result.kind == "verification_required":
                    return {"status": "failed", "error_code": "verification_required", "result": result}
                if _auth_error(result):
                    return {"status": "failed", "error_code": "auth_required", "result": result}
                if _clean_success(result):
                    break
                if _transient_error(result) and attempts < 3:
                    time.sleep(float(attempts * 2))
                    continue
                return {"status": "failed", "error_code": "search_remote_error", "result": result}

            total_num = max(total_num, int(result.payload.get("total_num") or 0))
            data = result.payload.get("data") or []
            if not isinstance(data, list):
                return {"status": "failed", "error_code": "search_response_malformed", "result": result}
            if page_no == 1 and not data and total_num == 0 and empty_confirmations == 0:
                empty_confirmations = 1
                confirmation = self._xhr(
                    method="POST",
                    path=SEARCH_PATH,
                    params=self._params(),
                    payload=payload,
                    write=False,
                )
                if _auth_error(confirmation):
                    return {"status": "failed", "error_code": "auth_required", "result": confirmation}
                if not _clean_success(confirmation):
                    return {"status": "failed", "error_code": "search_empty_unconfirmed", "result": confirmation}
                result = confirmation
                total_num = max(total_num, int(result.payload.get("total_num") or 0))
                data = result.payload.get("data") or []
                if not isinstance(data, list):
                    return {"status": "failed", "error_code": "search_response_malformed", "result": result}
            page_exact = 0
            for item in data:
                if not isinstance(item, dict):
                    continue
                product = item.get("campaign_product") or {}
                returned_pid = str(product.get("product_id") or "").strip()
                if returned_pid != pid:
                    continue
                campaign_id = str((item.get("campaign_info") or {}).get("campaign_id") or "").strip()
                key = (returned_pid, campaign_id)
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)
                page_exact += 1
            if not data or len(data) < page_size or (total_num and len(collected) >= total_num):
                break
            if page_no > 1 and page_exact == 0:
                break
        return {"status": "found" if collected else "not_found", "items": collected,
                "total_num": total_num, "result": result}

    @staticmethod
    def _joined_campaign_id(campaign: dict) -> str:
        return text_value(campaign.get("campaign_id") or campaign.get("id"))

    @staticmethod
    def _joined_campaign_type(campaign: dict) -> str:
        raw = text_value(
            campaign.get("crs_campaign_type")
            or campaign.get("crs_campaign_types")
        )
        return raw[:-2] if raw.endswith(".0") else raw

    @classmethod
    def _joined_campaign_allowed(cls, campaign: dict) -> bool:
        """只接收My、Seller与Exclusive；Global Selling和未知类型均排除。"""
        return cls._joined_campaign_type(campaign) in (
            JOINED_CAMPAIGN_COLLABORATION_TYPES
        )

    @classmethod
    def _joined_campaign_collaboration_type(cls, campaign: dict) -> str:
        return JOINED_CAMPAIGN_COLLABORATION_TYPES.get(
            cls._joined_campaign_type(campaign), ""
        )

    @classmethod
    def _joined_campaign_marked(cls, campaign: dict) -> str:
        raw_type = cls._joined_campaign_type(campaign)
        if raw_type in {"6", "7"}:
            return "0"
        if not raw_type:
            name_description = (
                f"{campaign.get('name', '')} {campaign.get('description', '')}"
            ).casefold()
            if any(marker in name_description for marker in (
                "tts funded", "tiktok shop", "hero product",
            )):
                return "0"
        return "false"

    @classmethod
    def _joined_campaign_summary(cls, campaign: dict) -> dict:
        summary = {
            "campaign_id": cls._joined_campaign_id(campaign),
            "collaboration_type": cls._joined_campaign_collaboration_type(campaign),
            "name": text_value(
                campaign.get("name")
                or campaign.get("campaign_name")
                or campaign.get("title")
            ),
        }
        for key in (
            "crs_campaign_type", "promotion_start_time", "promotion_end_time",
            "start_time", "end_time", "campaign_start_time", "campaign_end_time",
        ):
            if campaign.get(key) not in {None, ""}:
                summary[key] = campaign.get(key)
        return summary

    @staticmethod
    def _joined_product_summary(product: dict) -> dict:
        total_commission = product.get("total_commission_percent")
        if total_commission in {None, ""}:
            total_commission = product.get("partner_commission_percent")
        category = product.get("category") if isinstance(product.get("category"), dict) else {}
        return {
            "product_id": text_value(product.get("product_id")),
            "product_status": 2,
            "product_name": product.get("product_name") or "",
            "shop_name": product.get("shop_name") or "",
            "seller_id": product.get("seller_id"),
            "shop_code": product.get("shop_code"),
            "product_sales": product.get("product_sales"),
            "stock": product.get("stock"),
            "product_price": product.get("product_price"),
            "product_thumbnail": product.get("product_thumbnail"),
            "product_rating": product.get("product_rating"),
            "product_review_count": product.get("product_review_count"),
            "shop_score": product.get("shop_score"),
            "shop_experience_score": product.get("shop_experience_score"),
            "sample_quota": product.get("sample_quota"),
            "remaining_sample_budget": product.get("remaining_sample_budget"),
            "first_category_name": (
                product.get("first_category_name")
                or product.get("category_l1")
                or category.get("first_category_name")
                or category.get("name")
            ),
            "second_category_name": (
                product.get("second_category_name")
                or product.get("category_l2")
                or category.get("second_category_name")
            ),
            "third_category_name": (
                product.get("third_category_name")
                or product.get("category_l3")
                or category.get("third_category_name")
            ),
            "total_commission_percent": total_commission,
            "plan_commission_percent": product.get("plan_commission_percent"),
            "is_under_governed": product.get("is_under_governed") is True,
            "unavailable_type": product.get("unavailable_type"),
        }

    @classmethod
    def _joined_campaign_item(cls, *, campaign: dict, product: dict) -> dict:
        return {
            "campaign_product": cls._joined_product_summary(product),
            "campaign_info": cls._joined_campaign_summary(campaign),
            "has_free_sample": (
                product.get("has_free_sample") is True
                or int_value(product.get("sample_quota")) > 0
            ),
            "_source_pool": "joined_campaign",
            "_pick_up_selected": True,
        }

    @staticmethod
    def _cache_browser_result() -> BrowserResult:
        return BrowserResult(
            kind="cache",
            http_status=200,
            payload={"code": 0},
            dispatched=False,
            ambiguous=False,
        )

    @staticmethod
    def _cache_error_result(error_code: str) -> BrowserResult:
        return BrowserResult(
            kind="cache_error",
            http_status=0,
            payload={"code": str(error_code)},
            dispatched=False,
            ambiguous=False,
        )

    def _load_persistent_joined_campaign_index(self) -> dict:
        try:
            revision = self._cache_file_revision()
            cached = load_joined_campaign_cache(
                partner_id=self.identity.im_market_partner_id,
                path=self.joined_campaign_cache_path,
            )
        except JoinedCampaignCacheError as exc:
            error_code = str(exc or "joined_campaign_cache_unavailable")
            return {
                "status": "failed",
                "error_code": error_code,
                "result": self._cache_error_result(error_code),
            }
        self._joined_campaigns = []
        self._joined_campaign_index = dict(cached["index"])
        self._joined_campaign_indexed_at = time.monotonic()
        self._joined_campaign_cache_expires_at = cached["expires_at"].timestamp()
        self._joined_campaign_cache_ready = True
        self._joined_campaign_cache_revision = revision
        return {
            "status": "succeeded",
            "campaign_count": cached["campaign_count"],
            "pid_count": cached["pid_count"],
            "row_count": cached["row_count"],
            "product_pages_scanned": cached["product_pages_scanned"],
            "result": self._cache_browser_result(),
        }

    def _joined_read(self, *, path: str, params: dict[str, object],
                     remote_error: str) -> dict:
        attempts = 0
        while True:
            attempts += 1
            result = self._xhr(
                method="GET",
                path=path,
                params=params,
                payload=None,
                write=False,
            )
            if result.kind == "verification_required":
                return {
                    "status": "failed",
                    "error_code": "verification_required",
                    "result": result,
                }
            if _auth_error(result):
                return {
                    "status": "failed",
                    "error_code": "auth_required",
                    "result": result,
                }
            if _clean_success(result):
                return {"status": "succeeded", "result": result}
            if _transient_error(result) and attempts < 3:
                time.sleep(float(attempts * 2))
                continue
            return {
                "status": "failed",
                "error_code": remote_error,
                "result": result,
            }

    def _load_joined_campaigns(
        self,
        *,
        page_size: int = 1000,
        max_pages: int = 10,
        online_status: str | None = "2",
    ) -> dict:
        campaigns: list[dict] = []
        seen: set[str] = set()
        scanned = 0
        total_num = 0
        result = self._cache_browser_result()
        complete = False
        for page_no in range(1, max_pages + 1):
            params = self._params() | {
                "page_size": int(page_size),
                "cur_page": page_no,
                "campaign_join_status_category": "1",
                "crs_campaign_types": "",
            }
            if online_status is not None:
                params["campaign_online_status"] = online_status
            outcome = self._joined_read(
                path=JOINED_CAMPAIGN_LIST_PATH,
                params=params,
                remote_error="joined_campaign_list_remote_error",
            )
            if outcome["status"] == "failed":
                return outcome
            result = outcome["result"]
            data = result.payload.get("data") or {}
            if not isinstance(data, dict):
                return {
                    "status": "failed",
                    "error_code": "joined_campaign_list_malformed",
                    "result": result,
                }
            rows = data.get("campaign") or []
            if not isinstance(rows, list):
                return {
                    "status": "failed",
                    "error_code": "joined_campaign_list_malformed",
                    "result": result,
                }
            total_num = max(total_num, int_value(data.get("total_num")))
            if not rows and total_num > scanned:
                return {
                    "status": "failed",
                    "error_code": "joined_campaign_list_inconsistent",
                    "result": result,
                }
            scanned += len(rows)
            for campaign in rows:
                if not isinstance(campaign, dict):
                    continue
                campaign_id = self._joined_campaign_id(campaign)
                if (
                    not campaign_id
                    or campaign_id in seen
                    or not self._joined_campaign_allowed(campaign)
                ):
                    continue
                seen.add(campaign_id)
                campaigns.append(dict(campaign))
            if not rows or len(rows) < page_size or (total_num and scanned >= total_num):
                complete = True
                break
        if not complete:
            return {
                "status": "failed",
                "error_code": "joined_campaign_list_incomplete",
                "result": result,
            }
        return {
            "status": "found" if campaigns else "not_found",
            "campaigns": campaigns,
            "total_num": total_num,
            "result": result,
        }

    def _load_joined_campaign_products(
        self,
        campaign: dict,
        *,
        target_pid: str | None = None,
        page_size: int = 100,
        max_pages: int = 100,
        approved_only: bool = True,
    ) -> dict:
        campaign_id = self._joined_campaign_id(campaign)
        if not campaign_id:
            return {
                "status": "failed",
                "error_code": "joined_campaign_id_missing",
                "result": self._cache_browser_result(),
            }
        items: list[dict] = []
        scanned = 0
        total_num = 0
        result = self._cache_browser_result()
        complete = False
        for page_no in range(1, max_pages + 1):
            params = self._params() | {
                "cur_page": page_no,
                "page_size": int(page_size),
                "campaign_id": campaign_id,
                "marked": self._joined_campaign_marked(campaign),
            }
            if approved_only:
                params["product_status"] = "2"
            if target_pid is not None:
                params["product_id"] = str(target_pid)
            outcome = self._joined_read(
                path=JOINED_CAMPAIGN_PRODUCT_PATH,
                params=params,
                remote_error="joined_campaign_product_remote_error",
            )
            if outcome["status"] == "failed":
                return outcome
            result = outcome["result"]
            data = result.payload.get("data") or {}
            if not isinstance(data, dict):
                return {
                    "status": "failed",
                    "error_code": "joined_campaign_product_malformed",
                    "result": result,
                }
            rows = data.get("campaign_product") or []
            if not isinstance(rows, list):
                return {
                    "status": "failed",
                    "error_code": "joined_campaign_product_malformed",
                    "result": result,
                }
            total_num = max(total_num, int_value(data.get("total_num")))
            if not rows and total_num > scanned:
                return {
                    "status": "failed",
                    "error_code": "joined_campaign_product_inconsistent",
                    "result": result,
                }
            scanned += len(rows)
            for product in rows:
                if not isinstance(product, dict):
                    continue
                product_id = text_value(product.get("product_id"))
                if (
                    not product_id.isdigit()
                    or not 10 <= len(product_id) <= 32
                    or (approved_only and int_value(product.get("product_status")) != 2)
                ):
                    continue
                if target_pid is not None and product_id != str(target_pid):
                    continue
                items.append(self._joined_campaign_item(
                    campaign=campaign,
                    product=product,
                ))
            if target_pid is not None and items:
                complete = True
                break
            if not rows or len(rows) < page_size or (total_num and scanned >= total_num):
                complete = True
                break
        if not complete:
            return {
                "status": "failed",
                "error_code": "joined_campaign_product_incomplete",
                "result": result,
            }
        return {
            "status": "found" if items else "not_found",
            "items": items,
            "total_num": total_num,
            "pages_scanned": page_no,
            "result": result,
        }

    def _refresh_joined_campaign_index(
        self,
        *,
        campaign_page_size: int = 1000,
        product_page_size: int = 100,
        max_campaign_pages: int = 10,
        max_product_pages: int = 100,
        max_total_product_pages: int = 5000,
        progress_callback: Callable[[dict[str, int]], None] | None = None,
    ) -> dict:
        """完整读取一次joined Campaign货盘，后续PID查询只访问内存索引。"""
        listing = self._load_joined_campaigns(
            page_size=campaign_page_size,
            max_pages=max_campaign_pages,
        )
        if listing["status"] == "failed":
            return listing
        index: dict[str, list[dict]] = {}
        seen: set[tuple[str, str]] = set()
        result = listing["result"]
        product_pages_scanned = 0
        campaigns = list(listing.get("campaigns") or [])
        for campaign_ordinal, campaign in enumerate(campaigns, start=1):
            remaining_pages = int(max_total_product_pages) - product_pages_scanned
            if remaining_pages <= 0:
                return {
                    "status": "failed",
                    "error_code": "joined_campaign_index_budget_exhausted",
                    "result": result,
                }
            products = self._load_joined_campaign_products(
                campaign,
                page_size=product_page_size,
                max_pages=min(int(max_product_pages), remaining_pages),
            )
            if products["status"] == "failed":
                return products
            product_pages_scanned += int(products.get("pages_scanned") or 0)
            result = products["result"]
            for item in products.get("items") or []:
                product_id = text_value(
                    (item.get("campaign_product") or {}).get("product_id")
                )
                campaign_id = text_value(
                    (item.get("campaign_info") or {}).get("campaign_id")
                )
                key = (product_id, campaign_id)
                if not product_id or not campaign_id or key in seen:
                    continue
                seen.add(key)
                index.setdefault(product_id, []).append(item)
            if progress_callback is not None and (
                campaign_ordinal % 50 == 0
                or campaign_ordinal == len(campaigns)
            ):
                progress_callback({
                    "campaigns_done": campaign_ordinal,
                    "campaign_count": len(campaigns),
                    "product_pages_scanned": product_pages_scanned,
                    "pid_count": len(index),
                    "row_count": len(seen),
                })
        self._joined_campaigns = campaigns
        self._joined_campaign_index = index
        self._joined_campaign_indexed_at = time.monotonic()
        self._joined_campaign_cache_expires_at = 0.0
        self._joined_campaign_cache_ready = True
        return {
            "status": "succeeded",
            "campaign_count": len(campaigns),
            "pid_count": len(index),
            "product_pages_scanned": product_pages_scanned,
            "result": result,
        }

    def _cache_file_revision(self):
        try:
            stat = self.joined_campaign_cache_path.stat()
            return (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        except OSError:
            return None

    def _cache_file_changed(self) -> bool:
        previous = getattr(self, "_joined_campaign_cache_revision", None)
        return previous is not None and self._cache_file_revision() != previous

    def _search_joined_campaign_pid(self, pid: str, **refresh_options) -> dict:
        cache_fresh = bool(
            self._joined_campaign_cache_ready
            and not self._cache_file_changed()
            and (
                (
                    self._joined_campaign_cache_expires_at > 0
                    and time.time() < self._joined_campaign_cache_expires_at
                )
                or (
                    self._joined_campaign_cache_expires_at <= 0
                    and time.monotonic() - self._joined_campaign_indexed_at
                    < self.joined_campaign_cache_ttl_seconds
                )
            )
        )
        result = self._cache_browser_result()
        if not cache_fresh:
            loaded = self._load_persistent_joined_campaign_index()
            if loaded["status"] == "failed":
                if not self.allow_live_joined_campaign_refresh:
                    return loaded
                loaded = self._refresh_joined_campaign_index(**refresh_options)
                if loaded["status"] == "failed":
                    return loaded
            result = loaded["result"]
        items = list(self._joined_campaign_index.get(str(pid)) or [])
        return {
            "status": "found" if items else "not_found",
            "items": items,
            "total_num": len(items),
            "preview_source": "joined_campaign",
            "result": result,
        }

    def _joined_ui_market(self) -> str:
        query = parse_qs(urlparse(str(self.identity.warmup or "")).query)
        market = text_value((query.get("market") or [""])[0])
        return market if market.isdigit() else ""

    def joined_campaign_page_url(
        self,
        *,
        pid: str,
        campaign_id: str,
        campaign_type: int,
    ) -> str:
        path = JOINED_CAMPAIGN_PAGE_PATHS.get(int(campaign_type))
        market = self._joined_ui_market()
        if path is None or not market:
            return ""
        params: dict[str, object] = {
            "campaign_id": str(campaign_id),
            "market": market,
        }
        if int(campaign_type) == 1:
            params.update({
                "activeTab": "approved",
                "tab": "info",
                "page_size": 20,
                "cur_page": 1,
                "product_status": 2,
                "marked": "false",
                "approved_sub_tab": "individual",
                "product_id": str(pid),
            })
        elif int(campaign_type) == 5:
            params["product_id"] = str(pid)
        else:
            params.update({
                "tab": "details",
                "subTab": "product_pool",
                "cur_page": 1,
                "page_size": 100,
                "marked": 0,
            })
        return f"https://partner.tiktokshop.com{path}?{urlencode(params)}"

    def _dismiss_joined_page_overlays(self) -> None:
        for pattern in (
            r"^(我知道了|知道了)$",
            r"^(Got it|I know)$",
            r"^(Entendido|De acuerdo)$",
        ):
            try:
                button = self.page.get_by_role(
                    "button", name=re.compile(pattern, re.IGNORECASE),
                ).first
                if button.count() and button.is_visible():
                    button.click(timeout=2_000)
                    self.page.wait_for_timeout(200)
            except Exception:
                continue
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)
            self.page.keyboard.press("Escape")
        except Exception:
            pass

    def _filter_exclusive_page_pid(self, pid: str) -> bool:
        self._joined_page_probe_error = "exclusive_combo_missing"
        try:
            self.page.wait_for_timeout(5_000)
            combo = self.page.locator(
                '[data-e2e="c7730d29-f6ea-0f0d"]'
            ).first
            if not combo.count():
                combo = self.page.locator('[role="combobox"]').filter(
                    has_text=re.compile(
                        r"商品名称|产品名称|Product name|Nombre del producto",
                        re.IGNORECASE,
                    )
                ).first
            combo.wait_for(state="visible", timeout=30_000)
            self._dismiss_joined_page_overlays()
            self._joined_page_probe_error = "exclusive_combo_click_failed"
            combo.click(timeout=8_000, force=True)
            self.page.wait_for_timeout(500)
            if text_value(combo.get_attribute("aria-expanded")).casefold() != "true":
                combo.focus()
                self.page.keyboard.press("Enter")
                self.page.wait_for_timeout(500)
            self._joined_page_probe_error = "exclusive_option_missing"
            option = self.page.get_by_role("option").filter(
                has_text=re.compile(
                    r"^(商品\s*ID|产品\s*ID|Product ID|ID del producto)$",
                    re.IGNORECASE,
                )
            ).first
            try:
                option.wait_for(state="visible", timeout=8_000)
                option.click(timeout=3_000, force=True)
            except Exception:
                # 冷启动并发时下拉Portal可能晚于role树；当前默认“商品名称”，
                # 下一项固定为“商品 ID”，用键盘完成同一只读选择。
                combo.focus()
                self.page.keyboard.press("ArrowDown")
                self.page.keyboard.press("Enter")
                self.page.wait_for_timeout(300)
            self._joined_page_probe_error = "exclusive_input_missing"
            search_input = None
            inputs = self.page.locator("input:visible")
            for index in range(inputs.count()):
                candidate = inputs.nth(index)
                placeholder = text_value(candidate.get_attribute("placeholder"))
                if re.search(
                    r"商品\s*ID|产品\s*ID|Product ID|ID del producto",
                    placeholder,
                    re.IGNORECASE,
                ):
                    search_input = candidate
                    break
            if search_input is None:
                return False
            self._joined_page_probe_error = "exclusive_search_submit_failed"
            search_input.fill(str(pid))
            # 此页面的“筛选/Filter”按钮打开高级筛选抽屉，并不提交商品搜索；
            # Product ID 搜索由输入框 Enter 触发。
            search_input.press("Enter")
            self._joined_page_probe_error = "exclusive_pid_timeout"
            self.page.get_by_text(str(pid), exact=False).first.wait_for(
                state="visible",
                timeout=30_000,
            )
            self._joined_page_probe_error = ""
            return True
        except Exception:
            return False

    def prepare_joined_campaign_page(
        self,
        *,
        pid: str,
        campaign_id: str,
        campaign_type: int,
    ) -> dict:
        """进入真实Campaign商品池并确认页面中存在目标PID。"""
        self._joined_page_context = None
        self._joined_page_probe_error = ""
        url = self.joined_campaign_page_url(
            pid=str(pid),
            campaign_id=str(campaign_id),
            campaign_type=int(campaign_type),
        )
        if not url:
            error_code = "joined_campaign_page_type_unsupported"
            return {
                "status": "failed",
                "error_code": error_code,
                "result": self._cache_error_result(error_code),
            }
        try:
            self.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            self.page.wait_for_timeout(1_000)
        except Exception:
            error_code = "joined_campaign_page_navigation_failed"
            return {
                "status": "failed",
                "error_code": error_code,
                "result": self._cache_error_result(error_code),
            }
        if self._human_verification():
            return {
                "status": "failed",
                "error_code": "verification_required",
                "result": self._cache_error_result("verification_required"),
            }
        parsed = urlparse(str(self.page.url or ""))
        expected = urlparse(url)
        query = parse_qs(parsed.query)
        if any(marker in parsed.path.casefold() for marker in (
            "login", "passport", "signin",
        )):
            return {
                "status": "failed",
                "error_code": "auth_required",
                "result": self._cache_error_result("auth_required"),
            }
        if (
            parsed.scheme != "https"
            or parsed.hostname != "partner.tiktokshop.com"
            or parsed.path != expected.path
            or (query.get("campaign_id") or [""])[0] != str(campaign_id)
            or (query.get("market") or [""])[0] != self._joined_ui_market()
        ):
            error_code = "joined_campaign_page_redirected"
            return {
                "status": "failed",
                "error_code": error_code,
                "result": self._cache_error_result(error_code),
            }

        if int(campaign_type) == 7:
            pid_visible = self._filter_exclusive_page_pid(str(pid))
        else:
            try:
                target = self.page.get_by_text(str(pid), exact=False).first
                target.wait_for(state="visible", timeout=12_000)
                row = target.locator("xpath=ancestor::tr[1]")
                row_text = row.inner_text(timeout=5_000).casefold()
                pid_visible = any(marker in row_text for marker in (
                    "分享", "share", "复制", "copy",
                ))
            except Exception:
                pid_visible = False
        if not pid_visible:
            error_code = (
                f"joined_campaign_page_{self._joined_page_probe_error}"
                if self._joined_page_probe_error
                else "joined_campaign_page_pid_not_visible"
            )
            return {
                "status": "failed",
                "error_code": error_code,
                "result": self._cache_error_result(error_code),
            }
        self._joined_page_context = (
            str(pid), str(campaign_id), int(campaign_type),
        )
        return {
            "status": "ready",
            "result": self._cache_browser_result(),
        }

    def revalidate_joined_campaign(self, *, pid: str, campaign_id: str) -> dict:
        """生成前只读重查固定 Campaign，避免使用过期索引直接创建。"""
        listing = self._load_joined_campaigns()
        if listing["status"] == "failed":
            return listing
        campaign = next((
            item for item in listing.get("campaigns") or []
            if self._joined_campaign_id(item) == str(campaign_id)
        ), None)
        if campaign is None:
            return {
                "status": "not_found",
                "items": [],
                "error_code": "joined_campaign_not_active",
                "result": listing["result"],
            }
        products = self._load_joined_campaign_products(
            campaign,
            target_pid=str(pid),
        )
        if products["status"] == "not_found":
            products["error_code"] = "joined_campaign_pid_not_approved"
        return products

    def _search_opportunity_pid(self, pid: str) -> dict:
        payload = {
            "product_id": [str(pid)],
            "filter": {
                "product_source": [],
                "campaign_type": [],
                "label_type": [],
                "product_status": 1,
            },
            "page_size": 15,
            "page": 1,
        }
        last_result = None
        for clean_observation in range(2):
            attempts = 0
            while True:
                attempts += 1
                result = self._xhr(
                    method="POST",
                    path=OPPORTUNITY_SEARCH_PATH,
                    params=self._params(),
                    payload=payload,
                    write=False,
                )
                last_result = result
                if result.kind == "verification_required":
                    return {"status": "failed", "error_code": "verification_required", "result": result}
                if _auth_error(result):
                    return {"status": "failed", "error_code": "auth_required", "result": result}
                if _clean_success(result):
                    break
                if _transient_error(result) and attempts < 3:
                    time.sleep(float(attempts * 2))
                    continue
                return {"status": "failed", "error_code": "opportunity_search_remote_error", "result": result}

            data = result.payload.get("data") or {}
            if not isinstance(data, dict):
                return {"status": "failed", "error_code": "opportunity_search_malformed", "result": result}
            products = data.get("products") or []
            if not isinstance(products, list):
                return {"status": "failed", "error_code": "opportunity_search_malformed", "result": result}
            exact = next((
                product for product in products
                if isinstance(product, dict)
                and text_value(product.get("product_id")) == str(pid)
            ), None)
            if exact is not None:
                return {"status": "found", "product": exact, "result": result}
            if clean_observation == 0:
                continue
            return {
                "status": "not_found",
                "error_code": (
                    "opportunity_not_found"
                    if int_value(data.get("total")) <= 0 or not products
                    else "opportunity_exact_pid_not_found"
                ),
                "result": result,
            }
        return {"status": "not_found", "error_code": "opportunity_not_found", "result": last_result}

    def _opportunity_campaign(self, pid: str) -> dict:
        attempts = 0
        while True:
            attempts += 1
            result = self._xhr(
                method="GET",
                path=OPPORTUNITY_DETAIL_PATH,
                params=self._params() | {"product_id": str(pid)},
                payload=None,
                write=False,
            )
            if result.kind == "verification_required":
                return {"status": "failed", "error_code": "verification_required", "result": result}
            if _auth_error(result):
                return {"status": "failed", "error_code": "auth_required", "result": result}
            if _clean_success(result):
                break
            if _transient_error(result) and attempts < 3:
                time.sleep(float(attempts * 2))
                continue
            return {"status": "failed", "error_code": "opportunity_detail_remote_error", "result": result}

        detail = (result.payload.get("data") or {}).get("product_campaign_detail") or []
        if not isinstance(detail, list):
            return {"status": "failed", "error_code": "opportunity_detail_malformed", "result": result}
        best = choose_best_opportunity_campaign(detail)
        if best is None:
            return {"status": "not_found", "error_code": "opportunity_campaign_missing", "result": result}
        return {"status": "found", "item": best, "result": result}

    def _select_opportunity_campaign(self, *, pid: str, campaign_id: str) -> dict:
        result = self._xhr(
            method="POST",
            path=PICK_UP_SELECT_PATH,
            params=self._params(),
            payload={"product_id": str(pid), "campaign_id": str(campaign_id)},
            write=True,
        )
        if result.kind == "verification_required":
            return {"status": "failed", "error_code": "verification_required", "result": result}
        if _auth_error(result):
            return {"status": "failed", "error_code": "auth_required", "result": result}
        if _select_success(result):
            return {"status": "succeeded", "result": result}
        if result.ambiguous or _transient_error(result):
            return {"status": "result_unknown", "error_code": "pick_up_select_result_unknown", "result": result}
        return {"status": "failed", "error_code": "pick_up_select_rejected", "result": result}

    def _joined_campaign_item_from_cache(self, pid: str, campaign_id: str) -> dict:
        if (not self._joined_campaign_cache_ready or self._cache_file_changed()
                or (self._joined_campaign_cache_expires_at > 0
                    and time.time() >= self._joined_campaign_cache_expires_at)):
            loaded = self._load_persistent_joined_campaign_index()
            if loaded["status"] == "failed":
                return {}
        return next((
            item for item in self._joined_campaign_index.get(str(pid), [])
            if self._joined_campaign_id(
                dict(item.get("campaign_info") or {})
            ) == str(campaign_id)
        ), {})

    def _enrich_selected_sample_facts(
        self,
        pid: str,
        items: list[dict],
    ) -> list[dict]:
        enriched = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            campaign = dict(raw.get("campaign_info") or {})
            campaign_id = text_value(campaign.get("campaign_id"))
            joined = self._joined_campaign_item_from_cache(pid, campaign_id)
            joined_product = dict(joined.get("campaign_product") or {})
            product = dict(raw.get("campaign_product") or {})
            joined_quota = joined_product.get("sample_quota")
            if int_value(product.get("sample_quota")) <= 0 and int_value(joined_quota) > 0:
                product["sample_quota"] = joined_quota
                if joined_product.get("remaining_sample_budget") not in {None, ""}:
                    product["remaining_sample_budget"] = joined_product.get(
                        "remaining_sample_budget"
                    )
                enriched.append({
                    **raw,
                    "campaign_product": product,
                    "has_free_sample": True,
                })
            else:
                enriched.append(raw)
        return enriched

    @staticmethod
    def _opportunity_as_pickup_item(*, pid: str, product: dict,
                                    campaign_item: dict,
                                    joined_item: dict | None = None) -> dict:
        campaign = campaign_item.get("campaign") or {}
        joined_product = dict((joined_item or {}).get("campaign_product") or {})
        sample_quota = product.get("sample_quota")
        joined_quota = joined_product.get("sample_quota")
        if int_value(sample_quota) <= 0 and int_value(joined_quota) > 0:
            sample_quota = joined_quota
        remaining_sample_budget = product.get("remaining_sample_budget")
        if remaining_sample_budget in {None, ""}:
            remaining_sample_budget = joined_product.get("remaining_sample_budget")
        return {
            "campaign_product": {
                "product_id": str(pid),
                "product_status": 2,
                "product_name": (
                    product.get("product_name") or product.get("title") or ""
                ),
                "shop_name": product.get("shop_name") or "",
                "product_sales": product.get("product_sales") or product.get("sales"),
                "stock": product.get("stock"),
                "product_price": product.get("product_price") or product.get("price"),
                "product_thumbnail": (
                    product.get("product_thumbnail") or product.get("image")
                ),
                "product_rating": product.get("product_rating"),
                "product_review_count": product.get("product_review_count"),
                "shop_score": product.get("shop_score"),
                "shop_experience_score": product.get("shop_experience_score"),
                "sample_quota": sample_quota,
                "remaining_sample_budget": remaining_sample_budget,
                "total_commission_percent": campaign.get("commission"),
                "plan_commission_percent": campaign_item.get("open_collab_rate"),
                "is_under_governed": product.get("is_under_governed") is True,
                "unavailable_type": product.get("unavailable_type"),
            },
            "campaign_info": campaign,
            "has_free_sample": (
                product.get("has_free_sample") is True
                or campaign_item.get("has_free_sample") is True
                or (joined_item or {}).get("has_free_sample") is True
                or int_value(sample_quota) > 0
            ),
            "_source_pool": "opportunity",
            "_pick_up_selected": campaign_item.get("is_selected") is True,
        }

    def revalidate_selected_campaign(self, *, pid: str) -> dict:
        result = self._search_selected_pid(pid)
        if result["status"] == "found":
            result["items"] = self._enrich_selected_sample_facts(pid, list(result.get("items") or []))
        return result

    def search_pid(self, pid: str, *, page_size: int = 10,
                   max_pages: int = 20, onboard_missing: bool = False,
                   expected_campaign_id: str | None = None) -> dict:
        opportunity = self._search_opportunity_pid(pid)
        if opportunity["status"] == "failed":
            return opportunity

        campaign = (
            self._opportunity_campaign(pid)
            if opportunity["status"] == "found"
            else None
        )
        if campaign is not None and campaign["status"] == "failed":
            return campaign

        if (
            opportunity["status"] != "found"
            or campaign is None
            or campaign["status"] != "found"
        ):
            source = campaign if campaign is not None else opportunity
            if onboard_missing:
                return {
                    "status": "failed",
                    "error_code": "opportunity_repreview_required",
                    "result": source["result"],
                }
            joined = self._search_joined_campaign_pid(pid)
            if joined["status"] == "found":
                joined["opportunity_refresh_status"] = (
                    opportunity.get("error_code")
                    if opportunity["status"] != "found"
                    else campaign.get("error_code") if campaign is not None
                    else "opportunity_campaign_missing"
                )
            return joined

        # 已选商品池只属于“高机会命中”路线：用于确认最优Campaign是否已经
        # 添加，以及添加后回读当前最优候选。高机会干净未命中时不得进入这里。
        selected = self._search_selected_pid(
            pid,
            page_size=page_size,
            max_pages=max_pages,
        )
        if selected["status"] == "failed":
            return selected

        campaign_item = campaign["item"]
        campaign_data = campaign_item.get("campaign") or {}
        campaign_id = text_value(campaign_data.get("campaign_id"))
        if onboard_missing and expected_campaign_id and campaign_id != expected_campaign_id:
            return {"status": "failed", "error_code": "opportunity_repreview_required", "result": campaign["result"]}
        selected_items = self._enrich_selected_sample_facts(
            pid, list(selected.get("items") or []),
        )
        campaign_visible = any(
            text_value((item.get("campaign_info") or {}).get("campaign_id"))
            == campaign_id
            for item in selected_items
            if isinstance(item, dict)
        )

        if not onboard_missing:
            preview_items = selected_items
            if not campaign_visible:
                joined_item = self._joined_campaign_item_from_cache(
                    pid, campaign_id,
                )
                preview_items = [self._opportunity_as_pickup_item(
                    pid=pid,
                    product=opportunity["product"],
                    campaign_item=campaign_item,
                    joined_item=joined_item,
                ), *preview_items]
            return {
                "status": "found",
                "items": preview_items,
                "total_num": len(preview_items),
                "preview_source": "opportunity_and_selected",
                "opportunity_campaign_id": campaign_id,
                "result": campaign["result"],
            }

        if campaign_visible:
            selected["items"] = selected_items
            selected["opportunity_campaign_id"] = campaign_id
            selected["opportunity_campaign_already_selected"] = True
            return selected

        selection = {"status": "succeeded", "result": campaign["result"]}
        if campaign_item.get("is_selected") is not True:
            selection = self._select_opportunity_campaign(
                pid=pid,
                campaign_id=campaign_id,
            )
            if selection["status"] == "failed":
                return selection

        last_selected = selected
        for confirmation_no in range(3):
            last_selected = self._search_selected_pid(
                pid,
                page_size=page_size,
                max_pages=max_pages,
            )
            if last_selected["status"] == "found" and any(
                text_value((item.get("campaign_info") or {}).get("campaign_id")) == campaign_id
                for item in last_selected.get("items") or [] if isinstance(item, dict)
            ):
                last_selected["items"] = self._enrich_selected_sample_facts(
                    pid, list(last_selected.get("items") or []),
                )
                last_selected["selected_from_opportunity"] = True
                last_selected["opportunity_campaign_id"] = campaign_id
                return last_selected
            if last_selected["status"] == "failed":
                return last_selected
            if confirmation_no < 2:
                time.sleep(float(confirmation_no + 1))

        return {
            "status": "failed",
            "error_code": (
                "pick_up_select_result_unknown"
                if selection["status"] == "result_unknown"
                else "pick_up_not_visible_after_select"
            ),
            "result": last_selected["result"],
        }

    def create_link(self, *, pid: str, campaign_id: str,
                    creator_commission: Decimal,
                    require_joined_page: bool = False,
                    campaign_type: int | None = None) -> dict:
        if require_joined_page:
            expected_context = (
                str(pid), str(campaign_id), int(campaign_type or 0),
            )
            expected_page = urlparse(self.joined_campaign_page_url(
                pid=str(pid),
                campaign_id=str(campaign_id),
                campaign_type=int(campaign_type or 0),
            ))
            current_page = urlparse(str(self.page.url or ""))
            current_query = parse_qs(current_page.query)
            if (
                self._joined_page_context != expected_context
                or not expected_page.path
                or current_page.scheme != "https"
                or current_page.hostname != "partner.tiktokshop.com"
                or current_page.path != expected_page.path
                or (current_query.get("campaign_id") or [""])[0]
                != str(campaign_id)
                or (current_query.get("market") or [""])[0]
                != self._joined_ui_market()
            ):
                error_code = "joined_campaign_page_context_missing"
                return {
                    "status": "failed",
                    "error_code": error_code,
                    "result": self._cache_error_result(error_code),
                }
        params = self._params() | {
            "product_id": str(pid),
            "campaign_id": str(campaign_id),
            "creator_commission_percent": format(creator_commission.normalize(), "f"),
        }
        result = self._xhr(
            method="GET",
            path=LINK_PATH,
            params=params,
            payload=None,
            write=True,
        )
        if result.kind == "verification_required":
            return {"status": "failed", "error_code": "verification_required", "result": result}
        if result.ambiguous or _transient_error(result) or result.has_turing:
            return {"status": "result_unknown", "error_code": "create_result_unknown", "result": result}
        if _auth_error(result):
            return {"status": "failed", "error_code": "auth_required", "result": result}
        data = result.payload.get("data") or {}
        share_url = str(data.get("url") or "").strip() if isinstance(data, dict) else ""
        plan_id = str(data.get("plan_id") or "").strip() if isinstance(data, dict) else ""
        if (
            result.http_status == 200
            and result.payload.get("code") == 0
            and share_url.startswith("https://affiliate.tiktok.com/")
            and plan_id
        ):
            return {"status": "succeeded", "url": share_url, "plan_id": plan_id, "result": result}
        if result.http_status == 200 and result.payload.get("code") in {None, 0, "non_json", "malformed"}:
            # 平台声称成功，回执缺字段不能证明没有创建；禁止普通重试。
            return {"status": "result_unknown", "error_code": "create_receipt_incomplete", "result": result}
        return {"status": "failed", "error_code": "create_rejected", "result": result}


__all__ = [
    "BrowserResult",
    "JOINED_CAMPAIGN_LIST_PATH",
    "JOINED_CAMPAIGN_COLLABORATION_TYPES",
    "JOINED_CAMPAIGN_PRODUCT_PATH",
    "LINK_PATH",
    "OPPORTUNITY_DETAIL_PATH",
    "OPPORTUNITY_SEARCH_PATH",
    "PICK_UP_SELECT_PATH",
    "SEARCH_PATH",
    "ShareLinkBrowserTransport",
]
