"""线索、Campaign与可推资格分层。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_product_pool_qualification_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            alter table product_source_pool_version
                drop constraint if exists ck_product_source_pool_key
        """))
        connection.execute(text("""
            alter table product_source_pool_version
                add constraint ck_product_source_pool_key check (
                    pool_key in (
                        'full_managed','clue','campaign','durable','other'
                    )
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
                        'full_managed','campaign','clue','durable','other'
                    )
                )
        """))
        connection.execute(text("""
            update product_source_pool_version
               set status = 'archived'
             where bd_market = 'mx'
               and pool_key in ('durable','other')
               and status = 'active'
        """))


__all__ = ["apply_product_pool_qualification_v1"]
