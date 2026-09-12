"""Pure, allowlisted Profile diagnostics; no transport or persistence dependencies.

Values retain only the fields needed by the local completion probe. A returned
field shell is not data, and an authorized numeric zero is data. Money is kept
as exact decimal text with its original display/symbol; no currency or period
is inferred. Collection order in ``merge_profile_summaries`` is observation
order: the last actual value wins, while a later empty shell cannot erase it.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation


PROFILE_FIELDS = (
    "handle", "creator_oecuid", "selection_region", "follower_cnt",
    "med_gmv_revenue", "video_gmv", "live_gmv", "units_sold",
    "video_avg_view_cnt", "industry_groups", "content_groups", "top_video_data",
    "product_price_range", "video_publish_cnt_30d", "ec_video_publish_cnt_30d",
    "live_streaming_cnt_30d", "ec_live_streaming_cnt_30d", "gpm", "ec_live_gpm",
    "ec_video_gpm", "partnered_brand", "sales_performance_end_time",
)
_MONEY = frozenset({"med_gmv_revenue", "video_gmv", "live_gmv", "gpm", "ec_live_gpm", "ec_video_gpm"})
_COUNTS = frozenset({"follower_cnt", "units_sold", "video_avg_view_cnt", "video_publish_cnt_30d",
                     "ec_video_publish_cnt_30d", "live_streaming_cnt_30d", "ec_live_streaming_cnt_30d",
                     "sales_performance_end_time"})
_AVAILABLE = frozenset({"value", "zero"})
_STATUSES = _AVAILABLE | {"absent", "no_value", "unauthorized", "error"}
_VIDEO_KEYS = frozenset({"name", "video", "item_id", "like_cnt", "play_cnt", "comment_cnt", "release_date"})
_MISSING = object()


def _text(value, maximum=512):
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:maximum]


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    # Bound hostile/unexpected exponents without materializing enormous text.
    if number.adjusted() > 100 or number.as_tuple().exponent < -100:
        return None
    return format(number, "f")


def _envelope(value):
    """Return outer payload and status, respecting explicit platform metadata."""
    if value is _MISSING:
        return None, "absent"
    if isinstance(value, dict):
        if value.get("is_authorized") is False:
            return None, "unauthorized"
        if "status" in value and (isinstance(value["status"], bool) or value["status"] not in (0, "0")):
            return None, "error"
        if "value" in value:
            return value["value"], None
        if "status" in value or "is_authorized" in value:
            return None, "no_value"
    return value, None


def _nested(value):
    # Scalar metrics may arrive with multiple value wrappers. Money retains its
    # formatting dictionary and is handled by _money instead.
    for _ in range(8):
        if not isinstance(value, dict):
            return value, None
        nested, status = _envelope(value)
        if status:
            return None, status
        if nested is value:
            return value, None
        value = nested
    return None, "error"


def _money(value):
    metadata = {}
    for _ in range(8):
        if not isinstance(value, dict):
            amount = _decimal(value)
            return ({"decimal": amount, **metadata}, None) if amount is not None else (None, "error")
        if value.get("is_authorized") is False:
            return None, "unauthorized"
        if "status" in value and (isinstance(value["status"], bool) or value["status"] not in (0, "0")):
            return None, "error"
        for source, target in (("symbol", "rawSymbol"), ("format", "format"),
                               ("minimal_format", "minimumFormat"), ("maximum_format", "maximumFormat")):
            label = _text(value.get(source), 128)
            if label is not None:
                metadata[target] = label
        if "minimal" in value or "maximum" in value:
            minimum, maximum = _decimal(value.get("minimal")), _decimal(value.get("maximum"))
            if minimum is None or maximum is None or Decimal(minimum) > Decimal(maximum):
                return None, "error"
            return {"minimum": minimum, "maximum": maximum, **metadata}, None
        if "value" not in value:
            return None, "no_value"
        value = value["value"]
    return None, "error"


def _groups(value):
    if not isinstance(value, list):
        return None
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        group = {}
        identity = item.get("key", item.get("id"))
        if isinstance(identity, (str, int)) and not isinstance(identity, bool):
            label = _text(str(identity), 128)
            if label is not None:
                group["id"] = label
        label = _text(item.get("name", item.get("label")), 256)
        if label is not None:
            group["label"] = label
        weight = _decimal(item.get("value", item.get("weight")))
        if weight is not None:
            group["weight"] = weight
        if group:
            result.append(group)
    return result or None


def _brands(original, value):
    if isinstance(value, list):
        return {"brands": _groups(value) or []}
    if isinstance(value, bool):
        brands = original.get("brand") if isinstance(original, dict) else None
        return {"hasBrands": value, "brands": _groups(brands) or []}
    if isinstance(value, dict):
        brands = _groups(value.get("brand", value.get("brands")))
        return {"brands": brands} if brands else None
    return None


def _is_zero(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 0
    if isinstance(value, dict):
        if "decimal" in value:
            return Decimal(value["decimal"]) == 0
        if "minimum" in value and "maximum" in value:
            return Decimal(value["minimum"]) == Decimal(value["maximum"]) == 0
    return False


def _summarize_field(key, original):
    value, status = _envelope(original)
    if status:
        return {"status": status}
    if key in _MONEY:
        if value in (None, "", [], {}):
            return {"status": "no_value"}
        value, status = _money(value)
    elif key == "partnered_brand":
        value = _brands(original, value)
        status = None
    else:
        value, status = _nested(value)
        if status:
            return {"status": status}
        if value in (None, "", [], {}):
            return {"status": "no_value"}
        if key in _COUNTS:
            number = _decimal(value)
            if number is None or Decimal(number) != Decimal(number).to_integral_value():
                return {"status": "error"}
            value = int(Decimal(number))
        elif key in {"industry_groups", "content_groups"}:
            value = _groups(value)
        elif key == "top_video_data":
            if not isinstance(value, list):
                return {"status": "error"}
            value = {"count": len(value), "structureKeys": sorted({k for item in value if isinstance(item, dict)
                                                                    for k in item if k in _VIDEO_KEYS})}
        else:
            value = _text(value, 512)
            if key == "creator_oecuid" and value is not None and (not value.isascii() or not value.isdigit()):
                return {"status": "error"}
    if status:
        return {"status": status}
    if value is None:
        return {"status": "no_value"}
    return {"status": "zero" if _is_zero(value) else "value", "value": value}


def _identity(fields):
    def value(key):
        field = fields[key]
        return field.get("value") if field["status"] in _AVAILABLE else None
    market = value("selection_region")
    return {"oecId": value("creator_oecuid"), "handle": value("handle"),
            "market": market.lower() if market is not None else None}


def summarize_profile(profile: dict) -> dict:
    """Summarize exactly PROFILE_FIELDS, discarding all other raw properties."""
    if not isinstance(profile, dict):
        raise TypeError("profile must be a dictionary")
    fields = {key: _summarize_field(key, profile.get(key, _MISSING)) for key in PROFILE_FIELDS}
    return {"identity": _identity(fields), "fields": fields,
            "availableFields": [key for key in PROFILE_FIELDS if fields[key]["status"] in _AVAILABLE]}


def merge_profile_summaries(summaries) -> dict:
    """Merge in observation order, rejecting conflicts before merging any data.

    Only summaries returned by summarize_profile are accepted. Missing OEC or
    market may be supplied by another response, but two known values must agree.
    A handle may change under an unchanged OEC; the latest actual handle wins.
    """
    items = list(summaries)
    for item in items:
        if not isinstance(item, dict) or set(item) != {"identity", "fields", "availableFields"}:
            raise ValueError("invalid profile summary")
        fields = item["fields"]
        if not isinstance(fields, dict) or set(fields) != set(PROFILE_FIELDS):
            raise ValueError("invalid summary fields")
        if any(not isinstance(f, dict) or f.get("status") not in _STATUSES
               or set(f) - {"status", "value"} for f in fields.values()):
            raise ValueError("invalid summary field")
        if _identity(fields) != item["identity"]:
            raise ValueError("summary identity does not match fields")
    for key in ("oecId", "market"):
        known = {item["identity"][key] for item in items if item["identity"][key] is not None}
        if len(known) > 1:
            raise ValueError(f"profile identity conflict: {key}")
    fields = {key: {"status": "absent"} for key in PROFILE_FIELDS}
    for item in items:
        for key in PROFILE_FIELDS:
            candidate = item["fields"][key]
            if candidate["status"] in _AVAILABLE or fields[key]["status"] not in _AVAILABLE:
                if candidate["status"] != "absent":
                    fields[key] = deepcopy(candidate)
    return {"identity": _identity(fields), "fields": fields,
            "availableFields": [key for key in PROFILE_FIELDS if fields[key]["status"] in _AVAILABLE]}
