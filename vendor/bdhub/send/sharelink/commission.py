"""可审计的 ShareLink 达人佣金计算。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN

from .models import CommissionResult


VALID_MODES = frozenset({
    "MX_AVERAGE",
    "MAX_CREATOR",
    "KEEP_MARGIN",
    "BOOST_OVER_OPEN",
})
AVERAGE_POLICIES = frozenset({
    "FLOOR_INTEGER",
    "HALF_OR_PLUS_POINT_TWO",
})


def decimal_value(value: object) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        parsed = Decimal(str(value).replace("%", "").replace(",", "").strip())
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def raw_percent(value: object) -> Decimal:
    parsed = decimal_value(value)
    if parsed is None:
        return Decimal("0")
    return parsed / Decimal("100")


def percent_text(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.01")).normalize()
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


class CommissionEngine:
    def __init__(
        self,
        mode: str = "MX_AVERAGE",
        *,
        target_margin_pct: object = 1,
        boost_over_open_pct: object = 1,
        allow_zero_margin: bool = True,
        average_policy: str = "FLOOR_INTEGER",
    ) -> None:
        self.mode = str(mode or "").strip().upper()
        if self.mode not in VALID_MODES:
            raise ValueError("share_link_commission_mode_invalid")
        self.average_policy = str(average_policy or "").strip().upper()
        if self.average_policy not in AVERAGE_POLICIES:
            raise ValueError("share_link_average_policy_invalid")
        self.target_margin_pct = decimal_value(target_margin_pct)
        self.boost_over_open_pct = decimal_value(boost_over_open_pct)
        if self.target_margin_pct is None or self.target_margin_pct < 0:
            raise ValueError("share_link_target_margin_invalid")
        if self.boost_over_open_pct is None or self.boost_over_open_pct < 0:
            raise ValueError("share_link_boost_invalid")
        self.allow_zero_margin = bool(allow_zero_margin)

    def calculate(
        self,
        total_raw: object,
        open_raw: object,
        override_pct: object = None,
    ) -> CommissionResult:
        total_pct = raw_percent(total_raw)
        open_pct = raw_percent(open_raw)
        if decimal_value(open_raw) is None or open_pct < 0 or total_pct > 100 or open_pct > 100:
            return CommissionResult(False, error="commission_input_invalid")
        if total_pct <= 0:
            return CommissionResult(False, error="total_commission_missing")

        override = decimal_value(override_pct)
        if override is not None:
            if override <= 0 or override > total_pct:
                return CommissionResult(False, error="commission_override_out_of_range")
            creator_pct = override
            reason = "Excel Override"
        elif self.mode == "MAX_CREATOR":
            creator_pct = total_pct
            reason = "MAX_CREATOR"
        elif self.mode == "KEEP_MARGIN":
            creator_pct = total_pct - self.target_margin_pct
            reason = "KEEP_MARGIN"
        elif self.mode == "BOOST_OVER_OPEN":
            creator_pct = open_pct + self.boost_over_open_pct
            reason = "BOOST_OVER_OPEN"
        else:
            average = (total_pct + open_pct) / Decimal("2")
            if self.average_policy == "HALF_OR_PLUS_POINT_TWO":
                integer = average.quantize(Decimal("1"), rounding=ROUND_DOWN)
                creator_pct = (
                    average + Decimal("0.2")
                    if average == integer
                    else integer + Decimal("0.5")
                )
                creator_pct = min(creator_pct, total_pct)
                reason = "MX_AVERAGE_HALF_OR_PLUS_POINT_TWO"
            else:
                creator_pct = average.quantize(Decimal("1"), rounding=ROUND_DOWN)
                reason = "MX_AVERAGE"
            if creator_pct <= 0:
                creator_pct = total_pct
            elif open_pct > total_pct:
                creator_pct = total_pct
            elif creator_pct < open_pct:
                creator_pct = open_pct

        if creator_pct <= 0 or creator_pct > total_pct:
            return CommissionResult(False, error="creator_commission_out_of_range")
        if creator_pct < open_pct:
            return CommissionResult(False, error="creator_commission_below_open")
        margin = total_pct - creator_pct
        if not self.allow_zero_margin and margin <= 0:
            return CommissionResult(False, error="agency_margin_required")
        return CommissionResult(
            True,
            creator_pct=creator_pct,
            agency_margin_pct=margin,
            reason=reason,
        )


__all__ = [
    "CommissionEngine",
    "AVERAGE_POLICIES",
    "VALID_MODES",
    "decimal_value",
    "percent_text",
    "raw_percent",
]
