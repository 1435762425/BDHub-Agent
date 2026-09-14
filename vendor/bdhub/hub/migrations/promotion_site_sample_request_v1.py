from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_promotion_site_sample_request_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    statements = (
        """
        create table if not exists promotion_site_sample_request (
            request_id text primary key,
            bd_market text not null,
            creator_handle text not null,
            creator_handle_key text not null,
            pid text not null,
            release_id text not null,
            submitted_at timestamptz not null,
            synced_at timestamptz not null,
            constraint ck_promotion_site_sample_request_market check (bd_market = 'mx'),
            constraint ck_promotion_site_sample_request_handle check (creator_handle ~ '^[A-Za-z0-9._]{2,24}$'),
            constraint ck_promotion_site_sample_request_handle_key check (creator_handle_key = lower(creator_handle_key)),
            constraint ck_promotion_site_sample_request_pid check (pid ~ '^[0-9]{10,32}$')
        )
        """,
        """
        create index if not exists ix_promotion_site_sample_request_time
            on promotion_site_sample_request (submitted_at desc, request_id desc)
        """,
        """
        create index if not exists ix_promotion_site_sample_request_handle
            on promotion_site_sample_request (creator_handle_key, submitted_at desc)
        """,
        """
        create table if not exists promotion_site_sample_sync_state (
            bd_market text primary key,
            remote_origin text not null,
            cursor_submitted_at timestamptz,
            cursor_request_id text,
            last_synced_at timestamptz,
            last_error text,
            updated_at timestamptz not null,
            constraint ck_promotion_site_sample_sync_state_market check (bd_market = 'mx')
        )
        """,
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


__all__ = ["apply_promotion_site_sample_request_v1"]
