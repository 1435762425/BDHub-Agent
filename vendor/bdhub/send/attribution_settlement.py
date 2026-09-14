"""七天 TAP 加橱归因的纯状态判断。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True, slots=True)
class AttributionDecision:
    status: str
    matched_report_date: date | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CommerceAttributionDecision:
    """精确归因对的成交证据；缺任一对时不返回误导性的部分总数。"""

    state: str
    attributed_gmv: Decimal | None = None
    attributed_orders: int | None = None


def _plain_date(value: date, field: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{field}_date_required")
    return value


def decide_attribution_outcome(
    *,
    window_start_date: date,
    window_end_date: date,
    first_seen_report_date: date | None,
    sealed_report_dates: Iterable[date],
) -> AttributionDecision:
    """依据精确配对首次出现日与日报覆盖度决定当前归因状态。"""
    start = _plain_date(window_start_date, "window_start")
    end = _plain_date(window_end_date, "window_end")
    if end - start != timedelta(days=6):
        raise ValueError("seven_day_window_required")

    first_seen = None
    if first_seen_report_date is not None:
        first_seen = _plain_date(
            first_seen_report_date,
            "first_seen_report",
        )
        if first_seen < start:
            return AttributionDecision(
                status="ineligible",
                reason="preexisting_discovered_late",
            )
        if first_seen <= end:
            return AttributionDecision(
                status="matched",
                matched_report_date=first_seen,
            )

    sealed = {
        _plain_date(report_date, "sealed_report")
        for report_date in sealed_report_dates
    }
    expected = {
        start + timedelta(days=offset)
        for offset in range(7)
    }
    missing = sorted(expected - sealed)
    if not missing:
        return AttributionDecision(status="not_matched")

    if sealed and max(sealed) >= end:
        return AttributionDecision(
            status="data_incomplete",
            reason=(
                "missing_tap_report_dates:"
                + ",".join(day.isoformat() for day in missing)
            ),
        )
    return AttributionDecision(status="observing")


def settle_commerce_evidence(
    *,
    matched_pairs: int,
    gmv_evidence_pairs: int,
    order_evidence_pairs: int,
    attributed_gmv: Decimal | int | float | str | None,
    attributed_orders: int | None,
) -> CommerceAttributionDecision:
    """只在每个已匹配达人+PID 都有 TAP 指标时公开完整成交总数。"""
    counts = (matched_pairs, gmv_evidence_pairs, order_evidence_pairs)
    if any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in counts
    ) or not (
        0 <= gmv_evidence_pairs <= matched_pairs
        and 0 <= order_evidence_pairs <= matched_pairs
    ):
        raise ValueError("invalid_commerce_evidence_counts")
    if matched_pairs == 0:
        return CommerceAttributionDecision(state="not_applicable")
    if (
        gmv_evidence_pairs != matched_pairs
        or order_evidence_pairs != matched_pairs
    ):
        return CommerceAttributionDecision(state="data_incomplete")
    if attributed_gmv is None or attributed_orders is None:
        raise ValueError("complete_commerce_totals_required")
    if not isinstance(attributed_orders, int) or isinstance(
        attributed_orders, bool
    ):
        raise ValueError("complete_commerce_totals_required")
    try:
        normalized_gmv = Decimal(str(attributed_gmv))
    except (ArithmeticError, ValueError) as exc:
        raise ValueError("complete_commerce_totals_required") from exc
    return CommerceAttributionDecision(
        state="available",
        attributed_gmv=normalized_gmv,
        attributed_orders=attributed_orders,
    )


__all__ = [
    "AttributionDecision",
    "CommerceAttributionDecision",
    "decide_attribution_outcome",
    "settle_commerce_evidence",
]
