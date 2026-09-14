# -*- coding: utf-8 -*-
"""达人画像不可变观测契约与纯质量判定。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from .models import CreatorProfile, FIELDS


class CaptureKind(StrEnum):
    LIGHT = "light"
    FULL = "full"
    LEGACY_IMPORT = "legacy_import"


class SnapshotQuality(StrEnum):
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class CaptureContext:
    market: str
    kind: CaptureKind
    route: str
    expected_oec: str
    idempotency_key: str
    captured_at: datetime
    required_fields: frozenset[str] = frozenset({"handle", "oec_id"})
    min_raw_fields: int = 0
    transport_degraded: bool = False


@dataclass(frozen=True, slots=True)
class ProfileAssessment:
    quality: SnapshotQuality
    valid_fields: frozenset[str]
    rejection_reasons: tuple[str, ...]


FIELD_RAW_KEYS: dict[str, tuple[str, ...]] = {
    "handle": ("handle",),
    "oec_id": ("creator_oecuid",),
    "nickname": ("nickname",),
    "market": ("selection_region",),
    "followers": ("follower_cnt",),
    "main_category": ("main_industry", "industry_groups"),
    "bind_mcn": ("creator_bind_mcn_name",),
    "is_our_mcn": ("creator_bind_mcn_name",),
    "commission_rate": ("med_commission_rate_range",),
    "gmv_display": ("med_gmv_revenue",),
    "gmv_value": ("med_gmv_revenue",),
    "video_gmv": ("video_gmv",),
    "live_gmv": ("live_gmv",),
    "units_sold": ("units_sold",),
    "gpm": ("gpm",),
    "live_gpm": ("ec_live_gpm",),
    "video_gpm": ("ec_video_gpm",),
    "price_range": ("product_price_range",),
    "video_cnt_30d": ("video_publish_cnt_30d",),
    "ec_video_cnt_30d": ("ec_video_publish_cnt_30d",),
    "live_cnt_30d": ("live_streaming_cnt_30d",),
    "avg_view": ("video_avg_view_cnt",),
    "female_pct": ("follower_genders_v2",),
    "top_age": ("top_follower_age", "top_follower_ages"),
    "top_region": ("follower_state_location",),
    "contact_available": ("contact_info_available",),
    "is_fast_growing": ("is_fast_growing",),
    "is_active": ("is_active_creator",),
    "is_quickly_response": ("is_quickly_response",),
    "is_high_sample_dispatch": ("is_high_sample_dispatch_rate",),
    "brands": ("partnered_brand",),
    "labels": ("creator_labels", "sorted_creator_labels"),
    "bio": ("bio",),
    "email": ("bio",),
    "avatar_url": ("avatar",),
}

_NUMERIC_FIELDS = {
    "followers",
    "gmv_value",
    "video_gmv",
    "live_gmv",
    "units_sold",
    "gpm",
    "live_gpm",
    "video_gpm",
    "video_cnt_30d",
    "ec_video_cnt_30d",
    "live_cnt_30d",
    "avg_view",
    "female_pct",
}
_BOOLEAN_FIELDS = {
    "is_our_mcn",
    "contact_available",
    "is_fast_growing",
    "is_active",
    "is_quickly_response",
    "is_high_sample_dispatch",
}
_ZERO_CLUSTER_FIELDS = {
    "followers",
    "gmv_value",
    "video_gmv",
    "live_gmv",
    "units_sold",
    "gpm",
    "live_gpm",
    "video_gpm",
}
_REQUIRED_BY_MARKET = {
    "mx": frozenset({"handle", "oec_id", "followers", "gmv_value"}),
    "us": frozenset({"handle", "oec_id", "followers", "gmv_value"}),
    "jp": frozenset({"handle", "oec_id", "followers", "gmv_value"}),
    "uk": frozenset({"handle", "oec_id", "followers"}),
}
_DEFAULT_REQUIRED_FIELDS = frozenset({"handle", "oec_id", "followers"})
_MIN_RAW_FIELDS_BY_ROUTE = {
    "find_http": 20,
    "find_browser": 20,
    "find_profile2_browser": 20,
    "find_profile126_browser": 20,
    "find_profile126_pure_http": 20,
    "oec_http": 10,
    "oec_browser": 10,
    "mcn_relation_browser_2": 20,
    "mcn_relation_browser_126": 20,
    "resolved_oec_browser_2": 20,
    "known_oec_browser_126": 20,
    "resolved_oec_browser_126": 20,
    "oec_profile126_pure_http": 20,
    "http_control_16_2": 10,
    "browser_16": 10,
    "browser_file": 20,
    "send_oec_fallback": 20,
}


def required_profile_fields(market: str) -> frozenset[str]:
    """返回画像质量契约中该市场的一份合格观测必需字段。"""
    return _REQUIRED_BY_MARKET.get(market, _DEFAULT_REQUIRED_FIELDS)


def new_capture_context(
    profile: CreatorProfile,
    *,
    market: str,
    kind: CaptureKind,
    route: str,
    expected_oec: str | None = None,
    idempotency_key: str | None = None,
    captured_at: datetime | None = None,
    required_fields: frozenset[str] | None = None,
    min_raw_fields: int | None = None,
    transport_degraded: bool = False,
) -> CaptureContext:
    """在已接受响应边界生成显式、市场化的采集元数据。"""
    return CaptureContext(
        market=market,
        kind=kind,
        route=route,
        expected_oec=str(expected_oec or profile.oec_id or ""),
        idempotency_key=(idempotency_key or f"{route}:{uuid4().hex}"),
        captured_at=captured_at or datetime.now(timezone.utc),
        required_fields=(
            required_fields
            if required_fields is not None
            else required_profile_fields(market)
        ),
        min_raw_fields=(
            min_raw_fields
            if min_raw_fields is not None
            else _MIN_RAW_FIELDS_BY_ROUTE.get(route, 0)
        ),
        transport_degraded=transport_degraded,
    )


def _validate_context(context: CaptureContext) -> None:
    from .markets import MARKETS

    if context.market not in MARKETS:
        raise ValueError(f"不支持的市场: {context.market!r}")
    if not isinstance(context.kind, CaptureKind):
        raise ValueError(f"不支持的采集类型: {context.kind!r}")
    if not context.route.strip():
        raise ValueError("route 不能为空")
    if not context.expected_oec.strip():
        raise ValueError("expected_oec 不能为空")
    if not context.idempotency_key.strip():
        raise ValueError("idempotency_key 不能为空")
    if not isinstance(context.captured_at, datetime):
        raise ValueError("captured_at 必须是 datetime")
    if context.captured_at.tzinfo is None or context.captured_at.utcoffset() is None:
        raise ValueError("captured_at 必须带时区")
    unknown_required = context.required_fields - set(FIELDS)
    if unknown_required:
        raise ValueError(f"required_fields 含未知字段: {sorted(unknown_required)}")
    if context.min_raw_fields < 0:
        raise ValueError("min_raw_fields 不能为负数")


def _raw_value_is_explicit(value) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        if "value" in value:
            return value["value"] is not None
        return bool(value)
    return True


def _has_raw_evidence(raw: dict, field_name: str) -> bool:
    return any(
        key in raw and _raw_value_is_explicit(raw[key])
        for key in FIELD_RAW_KEYS[field_name]
    )


def _value_is_usable(field_name: str, value) -> bool:
    if field_name in _BOOLEAN_FIELDS:
        return isinstance(value, bool)
    if field_name in _NUMERIC_FIELDS:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            return False
        return math.isfinite(float(value)) and value >= 0
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def assess_profile(
    profile: CreatorProfile,
    context: CaptureContext,
) -> ProfileAssessment:
    """按原始证据判定哪些字段能推进 current；不修改画像或上下文。"""
    _validate_context(context)
    raw = profile.raw if isinstance(profile.raw, dict) else {}

    if context.transport_degraded:
        return ProfileAssessment(
            SnapshotQuality.DEGRADED,
            frozenset(),
            ("transport_degraded",),
        )
    if str(profile.oec_id) != context.expected_oec:
        return ProfileAssessment(
            SnapshotQuality.DEGRADED,
            frozenset(),
            ("oec_mismatch",),
        )

    valid_fields = frozenset(
        field_name
        for field_name in FIELDS
        if (
            context.kind is CaptureKind.LEGACY_IMPORT
            or _has_raw_evidence(raw, field_name)
        )
        and _value_is_usable(field_name, getattr(profile, field_name))
    )
    zero_cluster = {
        field_name
        for field_name in valid_fields & _ZERO_CLUSTER_FIELDS
        if getattr(profile, field_name) == 0
    }
    if (
        context.min_raw_fields > 0
        and len(raw) < context.min_raw_fields
        and len(zero_cluster) >= 3
    ):
        return ProfileAssessment(
            SnapshotQuality.DEGRADED,
            frozenset(),
            ("sparse_zero_cluster",),
        )

    missing = sorted(context.required_fields - valid_fields)
    if missing:
        return ProfileAssessment(
            SnapshotQuality.PARTIAL,
            valid_fields,
            tuple(f"missing_required:{field_name}" for field_name in missing),
        )
    return ProfileAssessment(SnapshotQuality.ACCEPTED, valid_fields, ())


__all__ = [
    "CaptureContext",
    "CaptureKind",
    "FIELD_RAW_KEYS",
    "ProfileAssessment",
    "SnapshotQuality",
    "assess_profile",
    "new_capture_context",
    "required_profile_fields",
]
