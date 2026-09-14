"""IM 多账号执行池：把任务与允许领取的账号显式绑定。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_im_operations_v9(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists send_task_account (
                task_id text not null references send_task(task_id) on delete cascade,
                account_name text not null,
                assigned_by text not null,
                assigned_at timestamptz not null,
                primary key (task_id, account_name)
            );
        """))
        connection.execute(text("""
            create index if not exists ix_send_task_account_account
                on send_task_account (account_name, task_id);
        """))


__all__ = ["apply_im_operations_v9"]
