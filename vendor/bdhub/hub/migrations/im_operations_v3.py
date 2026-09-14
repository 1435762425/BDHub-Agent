"""IM 运营后端统一：新增监听运行状态表。"""
from __future__ import annotations

from sqlalchemy.engine import Engine

from bdhub.hub.schema import im_listener_state


def apply_im_operations_v3(engine: Engine) -> None:
    """幂等创建监听状态表；新表不需要业务数据回填。"""
    if engine.dialect.name != "postgresql":
        return
    im_listener_state.create(engine, checkfirst=True)


__all__ = ["apply_im_operations_v3"]
