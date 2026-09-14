"""IM 运营模板渲染：为发送任务增加不可变模板快照。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_im_operations_v4(engine: Engine) -> None:
    """幂等扩展；旧任务保留 NULL，继续按自由文本语义运行。"""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text(
            "alter table send_task add column if not exists "
            "template_snapshot jsonb"
        ))


__all__ = ["apply_im_operations_v4"]
