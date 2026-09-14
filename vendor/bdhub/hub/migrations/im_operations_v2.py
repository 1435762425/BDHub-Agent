"""IM 运营 Phase 3B：人工 IM 状态从 MX 扩展到全部已注册市场。"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def apply_im_operations_v2(engine: Engine) -> None:
    """幂等移除旧库的 MX-only 约束，不读取或改写业务行。"""
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        if inspect(connection).has_table("creator_im_state"):
            connection.execute(text(
                "alter table creator_im_state "
                "drop constraint if exists ck_creator_im_state_market"
            ))


__all__ = ["apply_im_operations_v2"]
