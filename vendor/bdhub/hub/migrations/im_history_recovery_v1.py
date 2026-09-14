"""持久化 resident 历史恢复退避，跨浏览器回收保留进度。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_im_history_recovery_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists im_history_recovery_state (
                bd_market text not null,
                conversation_id text not null,
                failure_count integer not null,
                next_retry_at timestamptz not null,
                quarantined boolean not null default false,
                observed_update_ms bigint not null default 0,
                updated_at timestamptz not null,
                primary key (bd_market, conversation_id),
                constraint ck_im_history_recovery_failure_count
                    check (failure_count >= 1),
                constraint ck_im_history_recovery_observed_update
                    check (observed_update_ms >= 0)
            )
        """))
        connection.execute(text("""
            create index if not exists ix_im_history_recovery_due
                on im_history_recovery_state (
                    bd_market, quarantined, next_retry_at
                )
        """))


__all__ = ["apply_im_history_recovery_v1"]
