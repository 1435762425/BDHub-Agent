"""发送任务：持久化每位达人本次最多发送的商品卡数量。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_im_operations_v6(engine: Engine) -> None:
    """幂等增加商品卡上限；旧任务统一沿用默认值 2。"""
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        connection.execute(text("""
            alter table send_task
                add column if not exists card_limit_per_creator integer;
            update send_task
               set card_limit_per_creator = 2
             where card_limit_per_creator is null;
            alter table send_task
                alter column card_limit_per_creator set default 2;
            alter table send_task
                alter column card_limit_per_creator set not null;
        """))
        connection.execute(text("""
            do $$
            begin
                if not exists (
                    select 1
                    from pg_constraint
                    where conrelid = to_regclass('send_task')
                      and conname = 'ck_send_task_card_limit'
                ) then
                    alter table send_task
                        add constraint ck_send_task_card_limit
                        check (card_limit_per_creator between 1 and 4);
                end if;
            end
            $$;
        """))


__all__ = ["apply_im_operations_v6"]
