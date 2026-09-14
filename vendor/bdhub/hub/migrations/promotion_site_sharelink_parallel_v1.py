"""schema v62：推品网站ShareLink批次支持多resident账号有界并行。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_promotion_site_sharelink_parallel_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            alter table promotion_site_sharelink_batch
                add column if not exists account_names jsonb
        """))
        connection.execute(text("""
            update promotion_site_sharelink_batch
               set account_names = jsonb_build_array(account_name)
             where account_names is null
        """))
        connection.execute(text("""
            alter table promotion_site_sharelink_batch
                alter column account_names set not null
        """))
        connection.execute(text("""
            alter table promotion_site_sharelink_batch
                drop constraint if exists ck_promotion_site_sharelink_batch_accounts
        """))
        connection.execute(text("""
            alter table promotion_site_sharelink_batch
                add constraint ck_promotion_site_sharelink_batch_accounts check (
                    jsonb_typeof(account_names) = 'array'
                    and jsonb_array_length(account_names) between 1 and 3
                )
        """))
        connection.execute(text("""
            alter table promotion_site_sharelink_item
                add column if not exists account_name text
        """))
        connection.execute(text("""
            create index if not exists ix_promotion_site_sharelink_item_account
                on promotion_site_sharelink_item (
                    batch_id, account_name, status, sort_order, pid
                )
        """))


__all__ = ["apply_promotion_site_sharelink_parallel_v1"]
