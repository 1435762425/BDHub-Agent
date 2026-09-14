"""生产业务读适配；运营核心不持有可修改的达人或商品副本。"""
from __future__ import annotations

from sqlalchemy import func, select

from bdhub.hub.schema import creator_profile_current, creator_identity
from .contracts import OperationsError, PlanInput
from .matching import json_value


class BusinessSource:
    def __init__(self, engine, products):
        self.engine, self.products = engine, products

    def options(self):
        result = {}
        for pool in ("full_managed", "clue", "campaign"):
            data = self.products.list_products(source_pool=pool, limit=1)
            result[pool] = {"total": data["total"], "categories": data["filter_options"]["categories"],
                            "source_labels": data["filter_options"].get("source_labels", [])}
        with self.engine.connect() as connection:
            categories = connection.execute(self._creators_query().with_only_columns(creator_profile_current.c.main_category)).scalars().all()
        return {"pools": result, "creator_total": len(categories),
                "creator_categories": sorted({value for value in categories if value and value != "-1"})}

    @staticmethod
    def _creators_query(market: str = "mx"):
        p, identity = creator_profile_current, creator_identity
        source = p.join(identity, (p.c.bd_market == identity.c.bd_market) & (p.c.oec_id == identity.c.oec_id), full=True)
        return select(func.coalesce(p.c.oec_id, identity.c.oec_id).label("oec_id"),
                      func.coalesce(p.c.handle, identity.c.current_handle_key, "").label("handle"),
                      p.c.nickname, p.c.main_category, p.c.captured_at, p.c.field_sources).select_from(source).where(
                          func.coalesce(p.c.bd_market, identity.c.bd_market) == market)

    def snapshot(self, request: PlanInput):
        products = self.products.read_matching_products(
            source_pool=request.source_pool, source_label=request.source_label,
            category=request.product_category,
        )
        if not products:
            raise OperationsError("products_empty")
        if len(products) > 30000:
            raise OperationsError("product_scope_too_large")
        p = creator_profile_current
        query = self._creators_query()
        if request.creator_category:
            query = query.where(p.c.main_category == request.creator_category)
        creators = []
        with self.engine.connect() as connection:
            for row in connection.execute(query.order_by("oec_id").limit(100001)).mappings():
                creators.append({"oec_id": row["oec_id"], "handle": row["handle"], "nickname": row["nickname"],
                                 "main_category": row["main_category"], "captured_at": row["captured_at"],
                                 "category_observed_at": ((row["field_sources"] or {}).get("main_category") or {}).get("observed_at")})
        if not creators:
            raise OperationsError("creators_empty")
        if len(creators) > 100000:
            raise OperationsError("creator_scope_too_large")
        return json_value(products), json_value(creators)
