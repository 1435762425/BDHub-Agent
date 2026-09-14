"""IM 运营 Phase 1：补齐旧库兼容列和归因唯一索引。"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def apply_im_operations_v1(engine: Engine) -> None:
    """安全重复执行 IM 运营的存储兼容 DDL，不读取或改写业务行。"""
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        inspector = inspect(connection)
        if inspector.has_table("outreach"):
            connection.execute(text(
                "alter table outreach add column if not exists send_task_id text"
            ))
            connection.execute(text(
                "alter table outreach add column if not exists send_task_lead_id text"
            ))
            connection.execute(text(
                "alter table outreach add column if not exists logical_touch_id text"
            ))
            connection.execute(text(
                "create index if not exists ix_outreach_send_task "
                "on outreach(send_task_id, send_task_lead_id)"
            ))
        if inspector.has_table("send_task_attribution"):
            connection.execute(text(
                "create unique index if not exists "
                "uq_send_attribution_active_pair "
                "on send_task_attribution("
                "bd_market, creator_identity_key, pid"
                ") where status in ('reserved','observing','data_incomplete')"
            ))


__all__ = ["apply_im_operations_v1"]
