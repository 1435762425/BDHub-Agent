"""ShareLink 纯业务模型；不依赖数据库或浏览器。"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class CommissionResult:
    valid: bool
    creator_pct: Decimal = Decimal("0")
    agency_margin_pct: Decimal = Decimal("0")
    reason: str = ""
    error: str = ""


@dataclass(slots=True)
class Candidate:
    raw: dict[str, Any]
    original_index: int
    product_id: str
    product_name: str
    shop_name: str
    product_status: str
    sales: int
    stock: int
    product_price: Decimal | None
    product_thumbnail: str
    product_rating: Decimal | None
    product_review_count: int
    shop_score: Decimal | None
    shop_experience_score: Decimal | None
    campaign_id: str
    campaign_name: str
    campaign_type: int
    campaign_start_ms: int
    campaign_end_ms: int
    campaign_time_status: str
    total_pct: Decimal
    open_pct: Decimal
    sample_quota: int
    free_sample: bool
    governed: bool
    unavailable_type: str
    commission: CommissionResult
    valid: bool = False
    exclusion_reasons: tuple[str, ...] = ()
    selection_score: tuple[Any, ...] = field(default_factory=tuple)

    def public_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "shop_name": self.shop_name,
            "product_sales": self.sales,
            "stock": self.stock,
            "product_price": (
                str(self.product_price) if self.product_price is not None else None
            ),
            "product_thumbnail": self.product_thumbnail,
            "product_rating": (
                str(self.product_rating) if self.product_rating is not None else None
            ),
            "product_review_count": self.product_review_count,
            "shop_score": (
                str(self.shop_score) if self.shop_score is not None else None
            ),
            "shop_experience_score": (
                str(self.shop_experience_score)
                if self.shop_experience_score is not None else None
            ),
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "campaign_type": self.campaign_type,
            "campaign_start_ms": self.campaign_start_ms,
            "campaign_end_ms": self.campaign_end_ms,
            "campaign_time_status": self.campaign_time_status,
            "total_commission": str(self.total_pct),
            "open_commission": str(self.open_pct),
            "creator_commission": str(self.commission.creator_pct),
            "sample_quota": self.sample_quota,
            "agency_margin": str(self.commission.agency_margin_pct),
            "commission_reason": self.commission.reason,
            "free_sample": self.free_sample,
            "governed": self.governed,
            "unavailable_type": self.unavailable_type or None,
            "source_pool": str(self.raw.get("_source_pool") or "selected"),
            "pick_up_selected": self.raw.get("_pick_up_selected") is True,
            "valid": self.valid,
            "exclusion_reasons": list(self.exclusion_reasons),
        }


__all__ = ["Candidate", "CommissionResult"]
