"""一发/二发共享研究货盘；不写入正式商品、样品或发送表。"""
from __future__ import annotations

import calendar
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from bdhub import config
from bdhub.send.sharelink.candidates import campaign_time_status, https_url
from bdhub.send.sharelink.commission import decimal_value
from .kalodata import atomic_json, now, tokens

MARKETS = {"br": "BRL", "mx": "MXN"}
RUN_RE = re.compile(r"cc_[a-f0-9]{32}\Z")
TZ = ZoneInfo("Asia/Shanghai")


def cutoff_date(today: date | None = None) -> date:
    value = today or datetime.now(TZ).date()
    month = value.year * 12 + value.month - 1 + 2
    year, month0 = divmod(month, 12)
    return date(year, month0 + 1, min(value.day, calendar.monthrange(year, month0 + 1)[1]))


def campaign_eligibility(campaign: dict, *, at: datetime | None = None) -> tuple[bool, str, str]:
    current = at or datetime.now(timezone.utc)
    state, _, end_ms = campaign_time_status(campaign, now_ms=int(current.timestamp() * 1000))
    if not end_ms:
        return False, "活动截止日期缺失", ""
    try:
        end = datetime.fromtimestamp(end_ms / 1000, TZ).date()
    except (ValueError, OverflowError, OSError):
        return False, "活动截止日期无效", ""
    if state != "ACTIVE":
        return False, "活动尚未生效或已结束", end.isoformat()
    if end <= cutoff_date(current.astimezone(TZ).date()):
        return False, "剩余有效期不大于两个月", end.isoformat()
    return True, "", end.isoformat()


def sample_quantity(value: object) -> int | None:
    parsed = decimal_value(value) if not isinstance(value, bool) else None
    if parsed is None or parsed < 0 or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def money_value(value: object) -> str | None:
    if value is None:
        return None
    text = re.sub(r"[^0-9,.+-]", "", str(value))
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        text = "".join(parts[:-1]) + "." + parts[-1] if len(parts[-1]) <= 2 else "".join(parts)
    parsed = decimal_value(text)
    return format(parsed, "f") if parsed is not None and parsed >= 0 else None


def project_offer(raw: dict, market: str, *, at: datetime | None = None) -> tuple[dict | None, str]:
    product = raw.get("campaign_product") or {}
    campaign = raw.get("campaign_info") or {}
    valid, reason, end_date = campaign_eligibility(campaign, at=at)
    if not valid:
        return None, reason
    pid, campaign_id = str(product.get("product_id") or ""), str(campaign.get("campaign_id") or "")
    if not re.fullmatch(r"\d{19}", pid) or not re.fullmatch(r"\d{10,32}", campaign_id):
        return None, "商品或活动 ID 无效"
    if str(product.get("product_status")) != "2":
        return None, "商品未通过"
    total_raw = product.get("total_commission_percent")
    if total_raw in (None, ""):
        total_raw = product.get("partner_commission_percent")
    total, public = decimal_value(total_raw), decimal_value(product.get("plan_commission_percent"))
    if total is None or public is None or not (0 <= public <= 10000 and 0 < total <= 10000):
        return None, "佣金字段缺失或无效"
    if total <= public:
        return None, "没有正佣金差"
    if product.get("is_under_governed") is True or str(product.get("unavailable_type") or "0") not in {"0", "0.0"}:
        return None, "平台标记商品不可用"
    return project_inventory_offer(raw, market, at=at), ""


def project_inventory_offer(raw: dict, market: str, *, at: datetime | None = None) -> dict | None:
    """全部货盘只做字段规范化，佣金差、期限和状态留给筛选层。"""
    product = raw.get("campaign_product") or {}
    campaign = raw.get("campaign_info") or {}
    pid, campaign_id = str(product.get("product_id") or ""), str(campaign.get("campaign_id") or "")
    if not re.fullmatch(r"[0-9]{19}", pid) or not re.fullmatch(r"[0-9]{10,32}", campaign_id):
        return None
    current = at or datetime.now(timezone.utc)
    state, _, end_ms = campaign_time_status(campaign, now_ms=int(current.timestamp() * 1000))
    try:
        end_date = datetime.fromtimestamp(end_ms / 1000, TZ).date().isoformat() if end_ms else None
    except (ValueError, OverflowError, OSError):
        end_date = None
    total_raw = product.get("total_commission_percent")
    if total_raw in (None, ""):
        total_raw = product.get("partner_commission_percent")
    total, public = decimal_value(total_raw), decimal_value(product.get("plan_commission_percent"))
    total = total if total is not None and 0 <= total <= 10000 else None
    public = public if public is not None and 0 <= public <= 10000 else None
    available = str(product.get("product_status")) == "2" and product.get("is_under_governed") is not True and str(product.get("unavailable_type") or "0") in {"0", "0.0"} and state == "ACTIVE"
    quota = sample_quantity(product.get("sample_quota"))
    price = product.get("product_price") or {}
    rating = decimal_value(product.get("product_rating"))
    rating = rating if rating is not None and 0 <= rating <= 5 else None
    return {
        "offer_key": f"{pid}:{campaign_id}", "pid": pid, "market": market,
        "campaign_id": campaign_id, "campaign_name": str(campaign.get("name") or campaign.get("campaign_name") or ""),
        "campaign_type": str(campaign.get("crs_campaign_type") or ""), "end_date": end_date,
        "product_name": str(product.get("product_name") or ""), "shop_name": str(product.get("shop_name") or ""),
        "image_url": https_url(product.get("product_thumbnail")),
        "price_text": str(price.get("min_price") or "") if isinstance(price, dict) else str(price),
        "price_min": money_value(price.get("min_price")) if isinstance(price, dict) else money_value(price),
        "price_max": money_value(price.get("max_price")) if isinstance(price, dict) else money_value(price),
        "rating": float(rating) if rating is not None else None,
        "review_count": sample_quantity(product.get("product_review_count")),
        "currency": {**MARKETS, "it": "EUR", "uk": "GBP", "us": "USD", "jp": "JPY", "de": "EUR"}[market], "sales": sample_quantity(product.get("product_sales")),
        "public_commission": format(public / 100, "f") if public is not None else None,
        "total_commission": format(total / 100, "f") if total is not None else None,
        "commission_gap": format((total - public) / 100, "f") if total is not None and public is not None else None, "sample_quota": quota,
        "platform_available": available, "product_status": str(product.get("product_status") or ""), "campaign_state": state,
        "governed": product.get("is_under_governed") is True, "source_kind": str(raw.get("_source_pool") or "joined_campaign"),
        "first_eligible": quota is not None and quota > 0, "second_eligible": True,
        **({"binding_id": raw["_ready_binding"], "account": raw.get("_ready_account"), "list_id": raw.get("_ready_list_id")} if raw.get("_ready_binding") else {}),
    }


def inventory_view(snapshot, rules, *, layer="filtered", at=None):
    from .catalog_rules import validate_filters, matches_filters, filter_cutoff, version
    if layer not in {"all", "filtered"}:
        raise ValueError("catalog_layer_invalid")
    current = at or datetime.now(timezone.utc)
    rules = validate_filters(rules)
    result = {"snapshot_id": snapshot["snapshot_id"] if snapshot else None, "generated_at": snapshot["generated_at"] if snapshot else None,
              "rules": rules, "filter_version": version(rules), "cutoff_date": str(filter_cutoff(rules, current.astimezone(TZ).date()) or ""),
              "all_count": 0, "filtered_count": 0, "items": [], "stale": True, "layer": layer,
              "coverage": snapshot.get("coverage", "legacy_scope") if snapshot else "empty"}
    if not snapshot:
        return result
    result["stale"] = not timedelta(0) <= current - datetime.fromisoformat(snapshot["generated_at"]) <= timedelta(hours=36)
    offers = [o for rows in snapshot["index"].values() for raw in rows if (o := project_inventory_offer(raw, snapshot["market"], at=current))]
    matching = [o for o in offers if matches_filters(o, rules, today=current.astimezone(TZ).date())]
    result["all_count"] = len({o["pid"] for o in offers})
    result["filtered_count"] = len({o["pid"] for o in matching})
    chosen = offers if layer == "all" else matching
    chosen.sort(key=lambda o: (not o["platform_available"], o["commission_gap"] is None, -Decimal(o["commission_gap"] or "0"), -(date.fromisoformat(o["end_date"]).toordinal() if o["end_date"] else 0), o["campaign_id"]))
    by_pid = {}
    for offer in chosen:
        if offer["pid"] not in by_pid:
            by_pid[offer["pid"]] = {**offer, "offer_count": 0}
        by_pid[offer["pid"]]["offer_count"] += 1
    result["items"] = list(by_pid.values())
    result["first_count"] = sum(o["first_eligible"] for o in result["items"])
    result["second_count"] = len(result["items"])
    return result


class CatalogStore:
    def __init__(self, root: Path | None = None):
        self.root = root or config.ROOT / "data/research/campaign-catalog"

    def directory(self, run_id: str) -> Path:
        if not RUN_RE.fullmatch(run_id):
            raise ValueError("货盘同步编号无效。")
        return self.root / "runs" / run_id

    def create(self, run_id: str, market: str, account: str, *, runtime_dir: str = "") -> dict:
        if market not in MARKETS:
            raise ValueError("该市场尚未接入研究货盘。")
        directory = self.directory(run_id)
        directory.mkdir(parents=True, exist_ok=False)
        request = {"run_id": run_id, "market": market, "account": account, "created_at": now(), "runtime_dir": runtime_dir}
        atomic_json(directory / "request.json", request)
        self.state(run_id, {"state": "queued", "done": 0, "total": 0, "error": ""})
        return request

    def read_run(self, run_id: str) -> dict:
        directory = self.directory(run_id)
        request = json.loads((directory / "request.json").read_text())
        state_path = directory / "state.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {"state": "queued"}
        process = directory / "process.json"
        if process.exists():
            state["job_id"] = json.loads(process.read_text())["job_id"]
        if (directory / "stop").exists() and state["state"] in {"queued", "running"}:
            state["state"] = "stopping"
        return {**request, **state}

    def state(self, run_id: str, value: dict) -> None:
        atomic_json(self.directory(run_id) / "state.json", {**value, "updated_at": now()})

    def latest_run(self, market: str) -> dict | None:
        rows = []
        for path in (self.root / "runs").glob("cc_*/request.json"):
            try:
                row = self.read_run(path.parent.name)
            except (OSError, ValueError):
                continue
            if row["market"] == market:
                rows.append(row)
        return max(rows, key=lambda row: row["created_at"]) if rows else None

    def publish(self, run_id: str, index: dict, stats: dict) -> None:
        request = self.read_run(run_id)
        snapshot = {"snapshot_id": run_id, "market": request["market"], "generated_at": now(), "index": index, **stats}
        atomic_json(self.directory(run_id) / "snapshot.json", snapshot)
        atomic_json(self.root / f"{request['market']}.json", snapshot)

    def snapshot(self, market: str) -> dict | None:
        from .product_source_store import SourceStore, merge_selected_snapshot
        try:
            joined = self.joined_snapshot(market)
        except Exception:
            selected = SourceStore().selected_snapshot(market)
            if not selected:
                raise
            return selected
        return merge_selected_snapshot(joined, market)

    def joined_snapshot(self, market: str) -> dict | None:
        if market not in MARKETS:
            raise ValueError("该市场尚未接入研究货盘。")
        if market == "mx":
            full = self.root / "mx.json"
            if full.exists():
                local = json.loads(full.read_text())
                if local.get("coverage") == "all_joined":
                    return local
            from bdhub.hub.markets import identity_for
            from bdhub.send.sharelink.joined_campaign_cache import load_joined_campaign_cache
            cfg = config.load()
            account = next(a for a in config.load_accounts(cfg) if a.name == "acc1" and a.enabled)
            cache = load_joined_campaign_cache(partner_id=identity_for("mx", account=account, cfg=cfg).im_market_partner_id)
            stamp = cache["generated_at"].isoformat()
            return {"snapshot_id": f"mx:{stamp}", "market": market, "generated_at": stamp, "index": cache["index"], "campaign_count": cache["campaign_count"]}
        path = self.root / f"{market}.json"
        return json.loads(path.read_text()) if path.exists() else None


def catalog_view(snapshot: dict | None, purpose: str, *, at: datetime | None = None) -> dict:
    if purpose not in {"all", "first", "second"}:
        raise ValueError("货盘用途无效。")
    current = at or datetime.now(timezone.utc)
    base = {"snapshot_id": snapshot["snapshot_id"] if snapshot else None, "generated_at": snapshot["generated_at"] if snapshot else None,
            "cutoff_date": cutoff_date(current.astimezone(TZ).date()).isoformat(), "first_count": 0, "second_count": 0, "items": [], "stale": True}
    if not snapshot:
        return base
    generated = datetime.fromisoformat(snapshot["generated_at"])
    age = current - generated
    base["stale"] = not timedelta(0) <= age <= timedelta(hours=36)
    offers = []
    excluded: dict[str, int] = {}
    for rows in snapshot["index"].values():
        for raw in rows:
            offer, reason = project_offer(raw, snapshot["market"], at=current)
            if offer:
                offers.append(offer)
            else:
                excluded[reason] = excluded.get(reason, 0) + 1
    first_pids = {o["pid"] for o in offers if o["first_eligible"]}
    unknown_sample_pids = {o["pid"] for o in offers if o["sample_quota"] is None} - first_pids
    base["first_count"] = len(first_pids)
    base["second_count"] = len({o["pid"] for o in offers})
    base["sample_unknown_count"] = len(unknown_sample_pids)
    base["excluded"] = excluded
    selected = sorted((o for o in offers if purpose != "first" or o["first_eligible"]), key=lambda o: (-Decimal(o["commission_gap"]), -date.fromisoformat(o["end_date"]).toordinal(), o["campaign_id"]))
    by_pid: dict[str, dict] = {}
    for offer in selected:
        if offer["pid"] not in by_pid:
            by_pid[offer["pid"]] = {**offer, "offer_count": 0, "sample_status": "available" if offer["pid"] in first_pids else "unknown" if offer["pid"] in unknown_sample_pids else "none"}
        by_pid[offer["pid"]]["offer_count"] += 1
    base["items"] = list(by_pid.values())
    return base


def first_post_csv(snapshot: dict, *, offer_key: str, handles: str) -> str:
    view = catalog_view(snapshot, "first")
    if view["stale"]:
        raise ValueError("货盘超过 36 小时未更新，请先同步。")
    offer = next((row for row in view["items"] if row["offer_key"] == offer_key), None)
    if not offer:
        raise ValueError("该活动已不满足一发资格，请刷新货盘后重新选择。")
    names = tokens(handles, kind="handle", limit=1000)
    out = StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["handle", "pid", "Campaign ID", "样品额度", "公开佣金", "总佣金", "佣金差", "活动结束", "用途", "货盘版本"])
    for handle in names:
        writer.writerow([handle, offer["pid"], offer["campaign_id"], offer["sample_quota"], offer["public_commission"], offer["total_commission"], offer["commission_gap"], offer["end_date"], "一发样品邀约候选", snapshot["snapshot_id"]])
    return "\ufeff" + out.getvalue()
