"""商品卡发送成功后的墨西哥七天归因窗口。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


MEXICO_CITY = ZoneInfo("America/Mexico_City")


@dataclass(frozen=True, slots=True)
class AttributionWindow:
    start: date
    end: date


def window_for_card_sent_at(sent_at: datetime) -> AttributionWindow:
    if sent_at.tzinfo is None:
        raise ValueError("timezone_required")
    start = sent_at.astimezone(MEXICO_CITY).date()
    return AttributionWindow(start=start, end=start + timedelta(days=6))


__all__ = ["AttributionWindow", "MEXICO_CITY", "window_for_card_sent_at"]
