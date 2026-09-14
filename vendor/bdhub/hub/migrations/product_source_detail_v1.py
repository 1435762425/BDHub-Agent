"""来源商品池展示明细、店铺匹配与全托免Seller ID底座。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_product_source_detail_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        for statement in (
            "alter table product_source_pool_item add column if not exists category_l1 text",
            "alter table product_source_pool_item add column if not exists category_l2 text",
            "alter table product_source_pool_item add column if not exists category_l3 text",
            "alter table product_source_pool_item add column if not exists price_min numeric",
            "alter table product_source_pool_item add column if not exists price_max numeric",
            "alter table product_source_pool_item add column if not exists price_currency text",
            "alter table product_source_pool_item add column if not exists list_price_text text",
            "alter table product_source_pool_item add column if not exists sale_price_text text",
            "alter table product_source_pool_item add column if not exists stock_quantity bigint",
            "alter table product_source_pool_item add column if not exists source_attributes jsonb not null default '{}'::jsonb",
            "alter table product_catalog_item alter column seller_id drop not null",
            "alter table product_catalog_item add column if not exists source_pool_key text",
        ):
            connection.execute(text(statement))
        connection.execute(text("""
            alter table product_source_pool_item
                drop constraint if exists ck_product_source_pool_price
        """))
        connection.execute(text("""
            alter table product_source_pool_item
                add constraint ck_product_source_pool_price check (
                    (price_min is null or price_min >= 0)
                    and (price_max is null or price_max >= 0)
                    and (price_min is null or price_max is null or price_max >= price_min)
                )
        """))
        connection.execute(text("""
            alter table product_source_pool_item
                drop constraint if exists ck_product_source_pool_stock
        """))
        connection.execute(text("""
            alter table product_source_pool_item
                add constraint ck_product_source_pool_stock check (
                    stock_quantity is null or stock_quantity >= 0
                )
        """))
        connection.execute(text("""
            alter table product_catalog_item
                drop constraint if exists ck_product_catalog_source_pool
        """))
        connection.execute(text("""
            alter table product_catalog_item
                add constraint ck_product_catalog_source_pool check (
                    source_pool_key is null or source_pool_key in (
                        'full_managed','durable','other'
                    )
                )
        """))
        connection.execute(text("""
            create index if not exists ix_product_source_pool_item_category
                on product_source_pool_item (category_l1, category_l2, category_l3)
        """))
        connection.execute(text("""
            create index if not exists ix_product_source_pool_item_price
                on product_source_pool_item (price_min, price_max)
        """))
        connection.execute(text("""
            create index if not exists ix_product_catalog_source_pool
                on product_catalog_item (source_pool_key, pid)
        """))


__all__ = ["apply_product_source_detail_v1"]
