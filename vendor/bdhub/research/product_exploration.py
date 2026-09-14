"""第三方商品发现：只读 Kalodata 榜单，保存 PID 及可追溯的商品证据。"""
from __future__ import annotations

from datetime import date, timedelta
import json
import math
from pathlib import Path

from bdhub import config
from bdhub.hub.file_intents import FileIntentStore
from .kalodata import MARKETS, PID, number, observed_currency, tokens

TERMINAL = {"completed", "failed", "stopped", "interrupted"}


class ExplorationStore(FileIntentStore):
    def __init__(self, root: Path | None = None):
        super().__init__(root or config.ROOT / "data/research/product-exploration", prefix="pe", error_prefix="exploration")

    def read(self, job_id):
        job = super().read(job_id)
        process = self.path(job_id).with_suffix(".process")
        if process.exists():
            job.update(json.loads(process.read_text()))
        return job


def normalize_query(body, *, today=None):
    market = body.get("market")
    if market not in MARKETS:
        raise ValueError("不支持该探索市场。")
    mode = body.get("mode", "discover")
    if mode not in {"discover", "pids"}:
        raise ValueError("请选择榜单探索或已知 PID 核对。")
    targets = tokens(body.get("pids", ""), kind="pid", limit=20) if mode == "pids" else []
    keyword = body.get("keyword", "")
    if not isinstance(keyword, str) or len(keyword) > 150:
        raise ValueError("商品关键词请控制在 150 字以内。")
    days, limit, details = body.get("days", 7), body.get("limit", 100), body.get("detail_limit", 10)
    if type(days) is not int or days not in {7, 14, 30} or type(limit) is not int or limit not in {50, 100, 200}:
        raise ValueError("统计窗口支持 7/14/30 天，探索范围支持前 50/100/200 款。")
    if type(details) is not int or details not in {0, 10, 20}:
        raise ValueError("详情补充支持 0/10/20 款。")
    sort = body.get("sort", "sale")
    if sort not in {"sale", "revenue"}:
        raise ValueError("请选择销量或成交金额排序。")
    end = (today or date.today()) - timedelta(days=2)
    return {"market": market, "country": "GB" if market == "uk" else market.upper(), "currency": MARKETS[market],
            "keyword": keyword.strip(), "days": days, "start_date": str(end - timedelta(days=days-1)), "end_date": str(end),
            "sort": sort, "limit": limit, "detail_limit": details, "mode": mode, "targets": targets}


def full_service(value):
    # 只有字段值 1 是 Kalodata 明确展示全托管的证据，0 不替代 TikTok 的官方资格。
    return "marked" if type(value) is int and value == 1 else "unmarked" if type(value) is int and value == 0 else "unknown"


def metric(value):
    parsed = number(value)
    return float(parsed) if parsed is not None and parsed.is_finite() else None


def project_product(raw, query, rank):
    pid = raw.get("id")
    if not isinstance(pid, str) or not PID.fullmatch(pid):
        raise ValueError("Kalodata 商品结果缺少文本格式的完整 PID，已停止。")
    rating = metric(raw.get("product_rating"))
    if rating is not None and not 0 < rating <= 5:
        rating = None
    sales = metric(raw.get("sale"))
    row = {"pid": pid, "title": str(raw.get("product_title") or ""), "rank": rank,
           "sale": int(sales) if sales is not None else None, "revenue": str(raw.get("revenue") or ""),
           "price_min": str(raw.get("min_real_price") or ""), "price_max": str(raw.get("max_real_price") or ""),
           "unit_price": str(raw.get("unit_price") or ""), "rating": rating,
           "commission": str(raw.get("commission_rate") or ""), "creator_count": metric(raw.get("creator_num")),
           "launch_date": str(raw.get("launch_date") or ""), "shop_name": str(raw.get("name") or ""),
           "seller_id": str(raw.get("seller_id") or ""), "seller_type": str(raw.get("seller_type") or ""),
           "is_full_service": raw.get("is_full_service") if type(raw.get("is_full_service")) is int else None,
           "full_service": full_service(raw.get("is_full_service")), "delivery_type": str(raw.get("delivery_type") or "unknown"),
           "is_overseas": raw.get("is_overseas") if type(raw.get("is_overseas")) is int else None,
           "is_affiliate": raw.get("is_affiliate") if type(raw.get("is_affiliate")) is int else None,
           "category_ids": [str(raw[k]) for k in ("pri_cate_id", "sec_cate_id", "ter_cate_id") if raw.get(k)],
           "detail_checked": False, "url": f"https://www.kalodata.com/product/detail?id={pid}&region={query['country']}&language=zh-CN",
           "shop_url": "", "evidence_source": "Kalodata 商品榜单"}
    return decorate_quality(row)


def decorate_quality(row):
    sale = max(0, row.get("sale") or 0)
    rating = row.get("rating")
    row["priority_score"] = round(min(60, math.log10(sale + 1) * 15) + (rating or 0) * 6 + min(10, math.log10(max(0, row.get("creator_count") or 0) + 1) * 5))
    row["suggestion"] = "有出单，可验证合作资格" if sale > 0 and (rating is None or rating >= 4) else "评分偏低，谨慎选品" if rating is not None and rating < 4 else "本窗口缺少出单证据"
    if rating is None:
        row["suggestion"] += "；评分待补充"
    if row.get("is_affiliate") == 0:
        row["suggestion"] = "Kalodata 标记未开通联盟，需先核实合作方式"
    amount = row.get("price_min") or row.get("unit_price") or row.get("revenue") or ""
    row["observed_currency"] = "VND" if amount.startswith("₫") else observed_currency(amount)
    return row


def enrich_product(row, detail, query):
    if str(detail.get("id") or "") != row["pid"]:
        raise ValueError("商品详情 PID 不一致，未合并其他商品的信息。")
    rating = metric(detail.get("product_rating"))
    if rating is not None and 0 < rating <= 5:
        row["rating"] = rating
    for key, field in (("name", "shop_name"), ("seller_id", "seller_id"), ("seller_type", "seller_type"),
                       ("min_real_price", "price_min"), ("max_real_price", "price_max"), ("commission_rate", "commission")):
        if detail.get(key) is not None:
            row[field] = str(detail[key])
    if "is_full_service" in detail:
        row["is_full_service"] = detail["is_full_service"] if type(detail["is_full_service"]) is int else None
        row["full_service"] = full_service(detail["is_full_service"])
    row["delivery_type"] = str(detail.get("delivery_type") or row["delivery_type"])
    row["product_review_count"] = metric(detail.get("product_review_count"))
    if type(detail.get("is_affiliate")) is int:
        row["is_affiliate"] = detail["is_affiliate"]
    if type(detail.get("is_overseas")) is int:
        row["is_overseas"] = detail["is_overseas"]
    row["detail_checked"] = True
    row["evidence_source"] = "Kalodata 商品榜单 + 商品详情"
    if row["seller_id"].isdigit():
        row["shop_url"] = f"https://www.kalodata.com/shop/detail?id={row['seller_id']}&region={query['country']}&language=zh-CN"
    return decorate_quality(row)


def attach_catalog_status(rows, market):
    from .campaign_catalog import CatalogStore, inventory_view
    from .catalog_rules import filters_for
    if market not in {"mx", "br"}:
        return [{**r, "catalog_status": "unverified", "catalog_note": "带入官方商品池核验合作方案；研究结果不代表可推广"} for r in rows]
    try:
        source = CatalogStore().snapshot(market)
        view = inventory_view(source, filters_for(market))
        all_pids = set(source["index"]) if source else set()
        filtered = {r["pid"] for r in view["items"]} if not view["stale"] else set()
        return [{**r, "catalog_status": "filtered" if r["pid"] in filtered else "catalog" if r["pid"] in all_pids else "unverified",
                 "catalog_note": "符合当前货盘筛选" if r["pid"] in filtered else "货盘已收录，当前条件未通过或待更新" if r["pid"] in all_pids else "待高机会商品精确验证"} for r in rows]
    except (ValueError, OSError):
        return [{**r, "catalog_status": "unverified", "catalog_note": "当前货盘暂不可用，待平台验证"} for r in rows]


def filtered_rows(rows, *, search="", min_sales=0, min_rating=0, service="all", only_new=False):
    return [r for r in rows if (not search or search.casefold() in f"{r['pid']} {r['title']} {r['shop_name']}".casefold())
            and (not min_sales or (r["sale"] is not None and r["sale"] >= min_sales))
            and (not min_rating or (r["rating"] is not None and r["rating"] >= min_rating))
            and (service == "all" or r["full_service"] == service)
            and (not only_new or r["catalog_status"] in {"unverified", "unsupported"})]


def summary(job):
    rows = job["rows"]
    return {k: v for k, v in job.items() if k not in {"rows", "worker_job_id"}} | {
        "row_count": len(rows), "full_service_counts": {k: sum(r["full_service"] == k for r in rows) for k in ("marked", "unmarked", "unknown")}}
