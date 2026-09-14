"""从 MX 三份脚本提取的协议，所有 ID 与百分比都保持精确。"""
from __future__ import annotations

from decimal import Decimal
import re
from urllib.parse import urlsplit

CREATE_PATH = "/api/v1/affiliate/partner/campaign/product_list/create"
CARD_LIST_PATH = "/api/v1/affiliate/partner/im/product_list/list"


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,31}", value):
        raise ValueError("taplink_id_invalid")
    return value


def create_payload(*, pid: str, campaign_id: str, creator_pct: str, name: str, route: str) -> dict:
    identifier(pid)
    identifier(campaign_id)
    rate = Decimal(creator_pct) * 100
    if not rate.is_finite() or not 0 < rate <= 10000 or rate != rate.to_integral_value():
        raise ValueError("taplink_commission_invalid")
    if not name or len(name) > 50 or route not in {"campaign", "selected"}:
        raise ValueError("taplink_create_parameters_invalid")
    item = {"product_id": pid, "creator_commission_rate": str(int(rate))}
    payload = {"name": name, "campaign_id": campaign_id, "items": [item]}
    if route == "selected":
        payload.update(campaign_id="0", source=2)
        item["campaign_id"] = campaign_id
    return payload


def creation_receipt(payload: dict) -> dict:
    """创建回执不是卡片可用证据；仍需 IM 商品列表精确回读。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    listing = data.get("list") if isinstance(data, dict) else None
    if type(payload.get("code")) is not int or payload["code"] != 0 or not isinstance(listing, dict):
        raise ValueError("taplink_receipt_incomplete")
    url = listing.get("url")
    parsed = urlsplit(url) if isinstance(url, str) else None
    if (not parsed or parsed.scheme != "https" or parsed.hostname not in {"affiliate.tiktok.com", "partner.tiktokshop.com"}
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise ValueError("taplink_receipt_url_invalid")
    list_id = listing.get("product_list_id") or listing.get("id") or listing.get("list_id")
    # 部分脚本只保留 URL，缺 list_id 时按本任务唯一名称从 IM 列表恢复。
    return {"url": url, "list_id": identifier(str(list_id)) if list_id else None}


def find_card(payload: dict, *, pid: str, campaign_id: str, route: str, name: str, list_id: str | None = None, strict: bool = False) -> dict | None:
    if type(payload.get("code")) is not int or payload["code"] != 0:
        raise ValueError("taplink_card_lookup_failed")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise ValueError("taplink_card_lookup_malformed")
    matches = {}
    for row in data["list"]:
        if not isinstance(row, dict):
            continue
        lid = str(row.get("product_list_id") or "")
        if (list_id and lid != list_id) or row.get("product_list_name") != name:
            continue
        top_campaign = str(row.get("campaign_id") or "0")
        if top_campaign != (campaign_id if route == "campaign" else "0"):
            continue
        products = row.get("campaign_products")
        if not isinstance(products, list):
            continue
        exact = [p for p in products if isinstance(p, dict) and str(p.get("product_id") or "") == pid]
        if len(exact) != 1:
            continue
        product = exact[0]
        if strict and product.get("stock") is None:
            continue
        item_campaign = str(product.get("campaign_id") or (product.get("campaign_info") or {}).get("campaign_id") or "")
        if (route == "selected" and item_campaign != campaign_id) or (item_campaign and item_campaign != campaign_id):
            continue
        if product.get("stock") is not None and Decimal(str(product["stock"])) <= 0:
            continue
        if product.get("is_under_governed") is True or str(product.get("unavailable_type") or "0") not in {"0", "0.0"}:
            continue
        identifier(lid)
        matches[lid] = {"product_id": pid, "list_id": lid, "campaign_id": top_campaign,
                        "source_campaign_id": campaign_id, "campaign_name": str(row.get("campaign_name") or ""), "list_name": name}
    if len(matches) > 1:
        raise ValueError("taplink_card_binding_not_unique")
    return next(iter(matches.values()), None)
