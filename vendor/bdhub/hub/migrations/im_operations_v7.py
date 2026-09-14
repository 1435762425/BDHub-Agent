"""回复工作台：为人工出站队列增加图片消息字段。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_im_operations_v7(engine: Engine) -> None:
    """幂等增加图片元数据；历史出站记录统一回填为文字。"""
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        connection.execute(text("""
            alter table reply_outbox
                add column if not exists message_kind text,
                add column if not exists media_path text,
                add column if not exists media_name text,
                add column if not exists media_type text,
                add column if not exists media_size bigint;
            update reply_outbox
               set message_kind = 'text'
             where message_kind is null;
            alter table reply_outbox
                alter column message_kind set default 'text',
                alter column message_kind set not null;
        """))
        connection.execute(text("""
            do $$
            begin
                if not exists (
                    select 1
                      from pg_constraint
                     where conrelid = to_regclass('reply_outbox')
                       and conname = 'ck_reply_outbox_message_kind'
                ) then
                    alter table reply_outbox
                        add constraint ck_reply_outbox_message_kind
                        check (message_kind in ('text', 'image'));
                end if;
            end
            $$;
        """))


__all__ = ["apply_im_operations_v7"]
