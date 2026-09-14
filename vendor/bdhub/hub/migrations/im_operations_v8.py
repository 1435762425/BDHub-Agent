"""会话工作台：增加由 BD Hub 主导的互动已看水位。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_im_operations_v8(engine: Engine) -> None:
    """幂等增加本地互动已看时间；不读取或回写平台未读数。"""
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        connection.execute(text("""
            alter table if exists creator_im_state
                add column if not exists last_seen_interaction_at timestamptz;
        """))


__all__ = ["apply_im_operations_v8"]
