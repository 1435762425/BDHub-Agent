"""退役达人关系旧状态机字段。

关系阶段与三个历史状态列已经由多人多标签分类替代。迁移只删除旧状态机，
保留 WhatsApp、负责人、群组、备注和时间里程碑等仍在使用的运营事实。
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


RETIRED_COLUMNS = (
    "stage",
    "contact_status",
    "mcn_status",
    "tap_status",
)


def apply_retire_relationship_status_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        connection.execute(text(
            "drop index if exists ix_creator_relationship_stage"
        ))
        for column in RETIRED_COLUMNS:
            connection.execute(text(
                f"alter table creator_relationship "
                f"drop column if exists {column}"
            ))


__all__ = [
    "RETIRED_COLUMNS",
    "apply_retire_relationship_status_v1",
]
