"""Campaign 响应解析、过滤与确定性选优。"""
from __future__ import annotations

import re
import time
from decimal import Decimal
from typing import Iterable

from .commission import CommissionEngine, decimal_value, raw_percent
from .models import Candidate


_CAMPAIGN_START_KEYS = (
    "promotion_start_time",
    "start_time",
    "start_time_ms",
    "campaign_start_time",
    "campaign_start_time_ms",
    "effective_start_time",
    "effective_start_time_ms",
    "activity_start_time",
    "activity_start_time_ms",
    "valid_start_time",
    "valid_start_time_ms",
)
_CAMPAIGN_END_KEYS = (
    "promotion_end_time",
    "end_time",
    "end_time_ms",
    "campaign_end_time",
    "campaign_end_time_ms",
    "effective_end_time",
    "effective_end_time_ms",
    "activity_end_time",
    "activity_end_time_ms",
    "valid_end_time",
    "valid_end_time_ms",
)


def text_value(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"", "none", "nan"} else text


def int_value(value: object, default: int = 0) -> int:
    try:
        return int(Decimal(str(value).replace(",", "").strip()))
    except (TypeError, ValueError, ArithmeticError):
        return default


def _campaign_timestamp(campaign: dict, keys: tuple[str, ...]) -> int:
    for key in keys:
        value = int_value(campaign.get(key))
        if value:
            return value * 1000 if value < 100_000_000_000 else value
    return 0


def https_url(value: object) -> str:
    """兼容平台图片字段的字符串与 ``{url_list: [...]}`` 两种结构。"""
    if isinstance(value, dict):
        for key in ("url_list", "urls", "url", "origin_url"):
            clean = https_url(value.get(key))
            if clean:
                return clean
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            clean = https_url(item)
            if clean:
                return clean
        return ""
    clean = text_value(value)
    return clean if clean.startswith("https://") else ""


def price_value(value: object) -> Decimal | None:
    """提取 MX 商品起售价；平台当前返回带币种符号的 min/max 价格对象。"""
    if isinstance(value, dict):
        for key in ("min_price", "price", "sale_price", "amount", "max_price"):
            parsed = price_value(value.get(key))
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            parsed = price_value(item)
            if parsed is not None:
                return parsed
        return None
    clean = text_value(value)
    if not clean:
        return None
    numeric = re.sub(r"[^0-9,.+\-]", "", clean)
    if "," in numeric and "." in numeric:
        numeric = numeric.replace(",", "")
    elif numeric.count(",") == 1:
        whole, fraction = numeric.split(",", 1)
        numeric = f"{whole}.{fraction}" if len(fraction) <= 2 else whole + fraction
    else:
        numeric = numeric.replace(",", "")
    return decimal_value(numeric)


def campaign_time_status(
    campaign: dict,
    *,
    now_ms: int | None = None,
) -> tuple[str, int, int]:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    start = _campaign_timestamp(campaign, _CAMPAIGN_START_KEYS)
    end = _campaign_timestamp(campaign, _CAMPAIGN_END_KEYS)
    if start and now < start:
        return "NOT_STARTED", start, end
    if end and now > end:
        return "EXPIRED", start, end
    if start or end:
        return "ACTIVE", start, end
    return "UNKNOWN", start, end


def choose_best_opportunity_campaign(
    items: Iterable[dict],
    *,
    now_ms: int | None = None,
) -> dict | None:
    """为 ``pick_up/select`` 选择机会商品池中的最佳 Campaign。"""
    candidates: list[tuple[tuple[Decimal, Decimal, Decimal, Decimal], dict]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        campaign = raw.get("campaign") or {}
        if not isinstance(campaign, dict) or not text_value(campaign.get("campaign_id")):
            continue
        time_status, _start_ms, end_ms = campaign_time_status(
            campaign,
            now_ms=now_ms,
        )
        if time_status in {"EXPIRED", "NOT_STARTED"}:
            continue
        total_pct = raw_percent(campaign.get("commission"))
        candidates.append((
            (
                total_pct,
                Decimal(end_ms),
                Decimal(1 if raw.get("is_selected") is True else 0),
                Decimal(-index),
            ),
            raw,
        ))
    return max(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


def evaluate_candidates(
    items: Iterable[dict],
    *,
    product_id: str,
    commission_engine: CommissionEngine,
    override_pct: object = None,
    now_ms: int | None = None,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        product = raw.get("campaign_product") or {}
        campaign = raw.get("campaign_info") or {}
        returned_pid = text_value(product.get("product_id"))
        if returned_pid != product_id:
            continue
        product_status = text_value(product.get("product_status"))
        campaign_id = text_value(campaign.get("campaign_id"))
        campaign_type = int_value(campaign.get("crs_campaign_type"))
        time_status, start_ms, end_ms = campaign_time_status(
            campaign,
            now_ms=now_ms,
        )
        total_pct = raw_percent(product.get("total_commission_percent"))
        open_pct = raw_percent(product.get("plan_commission_percent"))
        commission = commission_engine.calculate(
            product.get("total_commission_percent"),
            product.get("plan_commission_percent"),
            override_pct,
        )
        governed = product.get("is_under_governed") is True
        unavailable = text_value(product.get("unavailable_type"))
        if unavailable.endswith(".0"):
            unavailable = unavailable[:-2]
        excluded: list[str] = []
        if not campaign_id:
            excluded.append("campaign_id_missing")
        if product_status not in {"", "2"}:
            excluded.append("product_status_unavailable")
        if governed:
            excluded.append("under_governance")
        if unavailable not in {"", "0"}:
            excluded.append("product_unavailable")
        if time_status == "EXPIRED":
            excluded.append("campaign_expired")
        if time_status == "NOT_STARTED":
            excluded.append("campaign_not_started")
        if not commission.valid:
            excluded.append(commission.error)
        candidate = Candidate(
            raw=raw,
            original_index=index,
            product_id=returned_pid,
            product_name=text_value(product.get("product_name")),
            shop_name=text_value(product.get("shop_name")),
            product_status=product_status,
            sales=int_value(product.get("product_sales")),
            stock=int_value(product.get("stock")),
            product_price=price_value(product.get("product_price")),
            product_thumbnail=https_url(product.get("product_thumbnail")),
            product_rating=decimal_value(product.get("product_rating")),
            product_review_count=int_value(product.get("product_review_count")),
            shop_score=decimal_value(product.get("shop_score")),
            shop_experience_score=decimal_value(
                product.get("shop_experience_score")
            ),
            campaign_id=campaign_id,
            campaign_name=text_value(campaign.get("name")),
            campaign_type=campaign_type,
            campaign_start_ms=start_ms,
            campaign_end_ms=end_ms,
            campaign_time_status=time_status,
            total_pct=total_pct,
            open_pct=open_pct,
            sample_quota=int_value(product.get("sample_quota")),
            free_sample=raw.get("has_free_sample") is True,
            governed=governed,
            unavailable_type=unavailable,
            commission=commission,
            valid=not excluded,
            exclusion_reasons=tuple(excluded),
        )
        if candidate.valid:
            # 与已选商品池 TAP Link 的候选选择一致：可用与活动状态已在
            # valid 门禁完成，随后只按 Total、到期时间和接口原始顺序选优。
            candidate.selection_score = (
                total_pct,
                Decimal(end_ms),
                Decimal(-index),
            )
        candidates.append(candidate)
    return candidates


def choose_best_candidate(candidates: Iterable[Candidate]) -> Candidate | None:
    valid = [candidate for candidate in candidates if candidate.valid]
    return max(valid, key=lambda candidate: candidate.selection_score) if valid else None


__all__ = [
    "campaign_time_status",
    "choose_best_candidate",
    "choose_best_opportunity_campaign",
    "evaluate_candidates",
    "int_value",
    "https_url",
    "price_value",
    "text_value",
]
