"""货盘筛选与建链规则；配置统一来自 config.yaml。"""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import calendar
import hashlib
import json
import os
from pathlib import Path
import re
import string
from uuid import uuid4

import yaml

from bdhub import config
from bdhub.hub.file_intents import FileIntentStore
from bdhub.send.sharelink.commission import CommissionEngine, decimal_value, percent_text
from .campaign_catalog import TZ


@dataclass(frozen=True, slots=True)
class CatalogFilters:
    keyword: str = ""
    min_gap: str | None = "0.01"
    expiry_mode: str = "months"
    expiry_value: int = 2
    end_after: str | None = None
    min_sales: int | None = None
    min_rating: str | None = None
    min_price: str | None = None
    max_price: str | None = None
    samples_only: bool = False
    available_only: bool = True


def validate_filters(value):
    if not isinstance(value, dict) or set(value) - set(asdict(CatalogFilters())):
        raise ValueError("catalog_filter_invalid")
    data = {**asdict(CatalogFilters()), **value}
    if not isinstance(data["keyword"], str) or len(data["keyword"]) > 200:
        raise ValueError("catalog_filter_invalid")
    for key, maximum in (("min_gap", 100), ("min_rating", 5), ("min_price", 10000000), ("max_price", 10000000)):
        if data[key] in (None, ""):
            data[key] = None
        else:
            parsed = decimal_value(data[key])
            if isinstance(data[key], bool) or parsed is None or not 0 <= parsed <= maximum:
                raise ValueError("catalog_filter_invalid")
            data[key] = str(parsed)
    if data["min_sales"] in (None, ""):
        data["min_sales"] = None
    elif type(data["min_sales"]) is not int or not 0 <= data["min_sales"] <= 10**12:
        raise ValueError("catalog_filter_invalid")
    if data["expiry_mode"] not in {"none", "days", "months", "date"} or type(data["expiry_value"]) is not int or not 0 <= data["expiry_value"] <= 3650:
        raise ValueError("catalog_filter_invalid")
    if data["end_after"] in (None, "") and data["expiry_mode"] != "date":
        data["end_after"] = None
    else:
        if not isinstance(data["end_after"], str):
            raise ValueError("catalog_filter_invalid")
        date.fromisoformat(data["end_after"])
    if type(data["samples_only"]) is not bool or type(data["available_only"]) is not bool:
        raise ValueError("catalog_filter_invalid")
    if data["min_price"] is not None and data["max_price"] is not None and Decimal(data["min_price"]) > Decimal(data["max_price"]):
        raise ValueError("catalog_price_range_invalid")
    return data


def filters_for(market):
    return validate_filters(config.load().catalog_workspace.filters.get(market, {}))


def version(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def filter_cutoff(rules, today=None):
    day = today or datetime.now(TZ).date()
    if rules["expiry_mode"] == "none":
        return None
    if rules["expiry_mode"] == "date":
        return date.fromisoformat(rules["end_after"])
    if rules["expiry_mode"] == "days":
        return day + timedelta(days=rules["expiry_value"])
    month = day.year * 12 + day.month - 1 + rules["expiry_value"]
    year, month0 = divmod(month, 12)
    return date(year, month0 + 1, min(day.day, calendar.monthrange(year, month0 + 1)[1]))


def matches_filters(offer, rules, *, today=None):
    if rules["keyword"].strip().casefold() not in " ".join(str(offer.get(k) or "") for k in ("pid", "product_name", "shop_name", "campaign_name")).casefold():
        return False
    if rules["available_only"] and not offer.get("platform_available"):
        return False
    if rules["samples_only"] and not (offer.get("sample_quota") is not None and offer["sample_quota"] > 0):
        return False
    for field, key in (("commission_gap", "min_gap"), ("sales", "min_sales"), ("rating", "min_rating"), ("price_min", "min_price")):
        if rules[key] is not None and (offer.get(field) is None or Decimal(str(offer[field])) < Decimal(str(rules[key]))):
            return False
    if rules["max_price"] is not None and (offer.get("price_min") is None or Decimal(offer["price_min"]) > Decimal(rules["max_price"])):
        return False
    cutoff = filter_cutoff(rules, today)
    return cutoff is None or bool(offer.get("end_date") and date.fromisoformat(offer["end_date"]) > cutoff)


def _save_workspace(update, *, root=None):
    """只替换本功能管理的 YAML 顶级段，不改动其他配置与手写注释。"""
    root = Path(root or config.ROOT)
    path = root / "config.yaml"
    guard = FileIntentStore(root / "data/runtime/catalog-settings", prefix="cw", error_prefix="catalog")
    with guard.lock():
        original = path.read_text()
        document = yaml.safe_load(original) or {}
        workspace = dict(document.get("catalog_workspace") or {})
        update(workspace)
        block = yaml.safe_dump({"catalog_workspace": workspace}, allow_unicode=True, sort_keys=False)
        match = re.search(r"(?m)^catalog_workspace:[^\n]*(?:\n|$)", original)
        if match:
            next_section = re.search(r"(?m)^\S", original[match.end():])
            end = match.end() + next_section.start() if next_section else len(original)
            updated = original[:match.start()] + block + "\n" + original[end:]
        else:
            updated = original.rstrip() + "\n\n" + block
        temp = path.with_name(f".config-catalog-{uuid4().hex}.tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, path.stat().st_mode & 0o777)
            with os.fdopen(fd, "w") as handle:
                handle.write(updated); handle.flush(); os.fsync(handle.fileno())
            if path.read_text() != original:
                raise ValueError("catalog_config_changed_retry")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def save_filters(market, value, *, root=None):
    if market not in {"mx", "br", "it", "uk", "us", "jp", "de"}:
        raise ValueError("catalog_market_invalid")
    rules = validate_filters(value)
    _save_workspace(lambda section: section.setdefault("filters", {}).__setitem__(market, rules), root=root)
    return rules


DEFAULT_LINK_RULES = [
    {"id": "average", "name": "平均分佣", "mode": "MX_AVERAGE", "average_policy": "FLOOR_INTEGER", "margin": "1", "boost": "1", "name_template": "BJN | {product_name} | {creator_commission}%"},
    {"id": "margin", "name": "保留机构利润", "mode": "KEEP_MARGIN", "average_policy": "FLOOR_INTEGER", "margin": "2", "boost": "1", "name_template": "BJN | {pid} | {creator_commission}%"},
    {"id": "boost", "name": "公开佣金上浮", "mode": "BOOST_OVER_OPEN", "average_policy": "FLOOR_INTEGER", "margin": "1", "boost": "2", "name_template": "BJN 升佣 | {creator_commission}% | {pid}"},
    {"id": "max", "name": "全部佣金给达人", "mode": "MAX_CREATOR", "average_policy": "FLOOR_INTEGER", "margin": "0", "boost": "0", "name_template": "BJN | {campaign_name} | {creator_commission}%"},
]


def validate_link_rule(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_LINK_RULES[0]):
        raise ValueError("taplink_rule_invalid")
    rule = {**DEFAULT_LINK_RULES[0], **value}
    if not isinstance(rule["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rule["id"]) or not isinstance(rule["name"], str) or not 1 <= len(rule["name"]) <= 60:
        raise ValueError("taplink_rule_invalid")
    for key in ("margin", "boost"):
        number = decimal_value(rule[key])
        if number is None or not 0 <= number <= 100 or number * 100 != (number * 100).to_integral_value():
            raise ValueError("taplink_rule_invalid")
        rule[key] = str(number)
    template = rule["name_template"]
    if not isinstance(template, str) or not template.strip() or len(template) > 200 or any(c in template for c in ("\n", "\r")):
        raise ValueError("taplink_name_invalid")
    for _, field, fmt, conversion in string.Formatter().parse(template):
        if field is not None and (field not in {"pid", "product_name", "campaign_name", "creator_commission", "date", "index"} or fmt or conversion):
            raise ValueError("taplink_name_field_invalid")
    engine_for(rule)
    return rule


def engine_for(rule):
    return CommissionEngine(rule["mode"], target_margin_pct=rule["margin"], boost_over_open_pct=rule["boost"], average_policy=rule["average_policy"])


def render_name(rule, offer, commission, index=1):
    return rule["name_template"].format(pid=offer["pid"], product_name=offer["product_name"], campaign_name=offer["campaign_name"],
        creator_commission=percent_text(Decimal(commission)), date=datetime.now(TZ).strftime("%Y%m%d"), index=index).strip()[:50]


def link_rules_for(market, route):
    saved = config.load().catalog_workspace.link_rules.get(market, {}).get(route, [])
    return [validate_link_rule(r) for r in saved] if saved else [dict(r) for r in DEFAULT_LINK_RULES]


def save_link_rule(market, route, value, *, root=None, make_default=False):
    if market not in {"mx", "br", "it", "uk", "us", "jp", "de"} or route not in {"campaign", "selected"}:
        raise ValueError("taplink_scope_invalid")
    rule = validate_link_rule(value)
    if type(make_default) is not bool:
        raise ValueError("taplink_rule_invalid")
    def update(section):
        routes = section.setdefault("link_rules", {}).setdefault(market, {})
        rules = routes.get(route) or [dict(r) for r in DEFAULT_LINK_RULES]
        # 新工作区显式记住当前选择；其它入口仅保存预设时保持原顺序语义。
        remaining = [r for r in rules if r["id"] != rule["id"]]
        rules = [rule] + remaining if make_default else remaining + [rule]
        if len(rules) > 30:
            raise ValueError("taplink_rule_limit")
        routes[route] = rules
    _save_workspace(update, root=root)
    return rule


def illustration(value):
    rule = validate_link_rule(value)
    commission = engine_for(rule).calculate(2000, 1000)
    offer = {"pid": "1730000000000000001", "product_name": "示例护发产品", "campaign_name": "示例商家活动"}
    formula = {"MX_AVERAGE": "（总佣金 + 公开佣金）÷ 2，按所选精度取值", "KEEP_MARGIN": "总佣金 − 机构保留百分点", "BOOST_OVER_OPEN": "公开佣金 + 上浮百分点", "MAX_CREATOR": "达人佣金 = 总佣金"}[rule["mode"]]
    return {"formula": formula, "total": "20", "public": "10", "creator": str(commission.creator_pct) if commission.valid else None,
            "margin": str(commission.agency_margin_pct) if commission.valid else None, "name": render_name(rule, offer, commission.creator_pct) if commission.valid else "示例佣金不满足范围，实际不合格商品会跳过", "valid": commission.valid}
