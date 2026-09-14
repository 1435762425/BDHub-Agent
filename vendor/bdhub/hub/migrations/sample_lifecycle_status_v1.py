"""schema v60：样品生命周期状态缓存，不抓已取消。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_sample_lifecycle_status_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text(
            "alter table sample_request_current "
            "add column if not exists lifecycle_status text not null "
            "default 'to_review'"
        ))
        connection.execute(text("""
            alter table sample_request_current
                drop constraint if exists ck_sample_request_lifecycle_status
        """))
        connection.execute(text("""
            alter table sample_request_current
                add constraint ck_sample_request_lifecycle_status check (
                    lifecycle_status in (
                        'to_review','ready_to_ship','shipped',
                        'content_pending','completed'
                    )
                )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_request_lifecycle
                on sample_request_current (
                    bd_market, lifecycle_status, review_deadline_at
                )
        """))


__all__ = ["apply_sample_lifecycle_status_v1"]
