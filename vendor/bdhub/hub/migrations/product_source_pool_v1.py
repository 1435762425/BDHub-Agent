"""全托、耐消和其他来源商品池的版本化底座。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_product_source_pool_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists product_source_pool_version (
                version_id text primary key,
                bd_market text not null,
                pool_key text not null,
                version_label text not null,
                source_name text not null,
                source_sha256 text not null,
                source_metadata jsonb not null default '{}'::jsonb,
                status text not null,
                row_count bigint not null,
                created_by text not null,
                activated_by text,
                created_at timestamptz not null,
                activated_at timestamptz,
                constraint ck_product_source_pool_market check (bd_market = 'mx'),
                constraint ck_product_source_pool_key check (
                    pool_key in ('full_managed','durable','other')
                ),
                constraint ck_product_source_pool_status check (
                    status in ('draft','active','archived','failed')
                ),
                constraint ck_product_source_pool_count check (row_count >= 0),
                constraint uq_product_source_pool_label unique (
                    bd_market, pool_key, version_label
                )
            )
        """))
        connection.execute(text("""
            create unique index if not exists uq_product_source_pool_active
                on product_source_pool_version (bd_market, pool_key)
                where status = 'active'
        """))
        connection.execute(text("""
            create index if not exists ix_product_source_pool_time
                on product_source_pool_version (bd_market, pool_key, created_at)
        """))
        connection.execute(text("""
            create table if not exists product_source_pool_item (
                version_id text not null,
                pid text not null,
                product_name text,
                image_url text,
                shop_name text,
                seller_id text,
                category text,
                product_url text,
                source_tags jsonb not null default '[]'::jsonb,
                source_refs jsonb not null default '{}'::jsonb,
                created_at timestamptz not null,
                primary key (version_id, pid),
                constraint fk_product_source_pool_item_version foreign key (version_id)
                    references product_source_pool_version(version_id),
                constraint ck_product_source_pool_item_pid check (
                    pid ~ '^[0-9]{10,32}$'
                ),
                constraint ck_product_source_pool_item_seller check (
                    seller_id is null or seller_id ~ '^[0-9]{10,32}$'
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_product_source_pool_item_pid
                on product_source_pool_item (pid, version_id)
        """))


__all__ = ["apply_product_source_pool_v1"]
