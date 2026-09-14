"""网站货盘Release草稿、Staging和Production审计。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_website_catalog_release_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists website_catalog_release (
                release_id text primary key,
                bd_market text not null,
                catalog_key text not null,
                status text not null,
                payload jsonb not null,
                source_sha256 text not null,
                product_count bigint not null,
                validation_summary jsonb not null,
                remote_release_id text,
                created_by text not null,
                created_at timestamptz not null,
                staged_at timestamptz,
                activated_at timestamptz,
                constraint ck_website_catalog_release_market check (bd_market = 'mx'),
                constraint ck_website_catalog_release_key check (
                    catalog_key in ('viral','opportunity','official')
                ),
                constraint ck_website_catalog_release_status check (
                    status in ('draft','staged','production','archived','failed')
                ),
                constraint ck_website_catalog_release_count check (product_count >= 0)
            )
        """))
        connection.execute(text("""
            create unique index if not exists uq_website_catalog_release_production
                on website_catalog_release (bd_market, catalog_key)
                where status = 'production'
        """))
        connection.execute(text("""
            create index if not exists ix_website_catalog_release_status
                on website_catalog_release (bd_market, status, created_at)
        """))


__all__ = ["apply_website_catalog_release_v1"]
