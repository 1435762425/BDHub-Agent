"""ShareLink 商品销量、图片、评分和当前 Campaign 数据。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_share_link_product_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists share_link_product_current (
                bd_market text not null,
                pid text not null,
                product_name text,
                shop_name text,
                product_thumbnail text,
                product_sales bigint,
                stock bigint,
                product_price numeric,
                product_rating numeric,
                product_review_count bigint,
                shop_score numeric,
                shop_experience_score numeric,
                selected_campaign_id text,
                selected_campaign_name text,
                total_commission numeric,
                open_commission numeric,
                creator_commission numeric,
                agency_margin numeric,
                campaign_end_at timestamptz,
                free_sample boolean,
                source_job_id text,
                captured_at timestamptz not null,
                platform_captured_at timestamptz,
                primary key (bd_market, pid),
                constraint ck_share_link_product_current_market check (
                    bd_market in ('mx','br','uk','it','jp','us','de')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_share_link_product_current_sales
                on share_link_product_current (bd_market, product_sales)
        """))
        connection.execute(text("""
            create index if not exists ix_share_link_product_current_rating
                on share_link_product_current (bd_market, product_rating)
        """))


__all__ = ["apply_share_link_product_v1"]
