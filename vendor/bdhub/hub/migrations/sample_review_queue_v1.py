"""schema v61：单条样品审批可等待账号恢复并自动接续。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_sample_review_queue_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            alter table sample_review_attempt
                drop constraint if exists ck_sample_review_status
        """))
        connection.execute(text("""
            alter table sample_review_attempt
                add constraint ck_sample_review_status check (
                    status in (
                        'waiting_account','starting','started',
                        'succeeded','result_unknown','failed'
                    )
                )
        """))
        connection.execute(text("""
            create unique index if not exists uq_sample_review_active_request
                on sample_review_attempt (request_id)
                where status in ('waiting_account','starting','started')
        """))


__all__ = ["apply_sample_review_queue_v1"]
