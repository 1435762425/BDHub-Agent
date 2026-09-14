"""回复工作台：扩展人工 IM 状态为待处理、处理中、已完成和拒绝。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_im_operations_v5(engine: Engine) -> None:
    """幂等扩展回复状态约束；已有状态和值保持不变。"""
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        connection.execute(text("""
            do $$
            begin
                if to_regclass('creator_im_state') is not null
                   and not exists (
                       select 1
                       from pg_constraint
                       where conrelid = to_regclass('creator_im_state')
                         and conname = 'ck_creator_im_state_status'
                         and pg_get_constraintdef(oid) like '%processing%'
                   )
                then
                    alter table creator_im_state
                        drop constraint if exists ck_creator_im_state_status;
                    alter table creator_im_state
                        add constraint ck_creator_im_state_status
                        check (
                            status in (
                                'pending_reply',
                                'processing',
                                'replied',
                                'rejected'
                            )
                        );
                end if;
            end
            $$;
        """))


__all__ = ["apply_im_operations_v5"]
