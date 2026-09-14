from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_promotion_site_sample_request_v2(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    statements = (
        "alter table promotion_site_sample_request add column if not exists source_pool_key text not null default 'full_managed'",
        "alter table promotion_site_sample_request add column if not exists page_slug text not null default 'tiktok-oficial'",
        """
        do $$ begin
          if not exists (
            select 1 from pg_constraint
             where conname = 'ck_promotion_site_sample_request_source_pool'
          ) then
            alter table promotion_site_sample_request
              add constraint ck_promotion_site_sample_request_source_pool
              check (source_pool_key in ('full_managed','campaign'));
          end if;
        end $$
        """,
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


__all__ = ["apply_promotion_site_sample_request_v2"]
