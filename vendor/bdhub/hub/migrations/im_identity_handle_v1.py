"""Official IM mget identity observations."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_im_identity_handle_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists im_creator_identity (
                bd_market text not null,
                creator_oec_id text not null,
                handle text not null,
                nickname text,
                handle_status text,
                handle_authorized boolean,
                source text not null,
                first_seen_at timestamptz not null,
                last_seen_at timestamptz not null,
                last_conversation_id text,
                primary key (bd_market, creator_oec_id),
                constraint ck_im_creator_identity_source
                    check (source = 'im_creator_mget')
            )
        """))
        connection.execute(text("""
            create index if not exists ix_im_creator_identity_handle
                on im_creator_identity (bd_market, handle)
        """))
        connection.execute(text("""
            create index if not exists ix_im_creator_identity_seen
                on im_creator_identity (bd_market, last_seen_at)
        """))


__all__ = ["apply_im_identity_handle_v1"]
