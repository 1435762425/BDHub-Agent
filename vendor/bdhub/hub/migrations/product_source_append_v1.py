"""允许全托与耐消来源按命名标签累积，Campaign仍保持单一当前版本。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_product_source_append_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            drop index if exists uq_product_source_pool_active
        """))
        connection.execute(text("""
            create unique index if not exists
                uq_product_source_pool_active_campaign
            on product_source_pool_version (bd_market, pool_key)
            where status = 'active' and pool_key = 'campaign'
        """))
        connection.execute(text("""
            update product_source_pool_version
               set source_metadata = jsonb_set(
                   coalesce(source_metadata, '{}'::jsonb),
                   '{source_label}',
                   to_jsonb(
                       coalesce(
                           nullif(regexp_replace(source_name, '\\.[^.]+$', ''), ''),
                           version_label
                       )
                   ),
                   true
               )
             where pool_key in ('full_managed', 'clue')
               and nullif(trim(source_metadata->>'source_label'), '') is null
        """))


__all__ = ["apply_product_source_append_v1"]
