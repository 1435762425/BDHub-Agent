"""单会话按需同步命令队列。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_im_operations_v10(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists im_conversation_sync_request (
                request_id text primary key,
                bd_market text not null,
                identity text not null,
                oec_id text not null,
                conversation_id text,
                requested_count integer not null,
                status text not null,
                error text,
                created_at timestamptz not null,
                started_at timestamptz,
                finished_at timestamptz,
                updated_at timestamptz not null,
                constraint ck_im_conversation_sync_request_status
                    check (status in ('queued','running','succeeded','failed')),
                constraint ck_im_conversation_sync_request_count
                    check (requested_count between 1 and 1000)
            )
        """))
        connection.execute(text("""
            create index if not exists ix_im_conversation_sync_request_queue
                on im_conversation_sync_request (bd_market, status, created_at)
        """))


__all__ = ["apply_im_operations_v10"]
