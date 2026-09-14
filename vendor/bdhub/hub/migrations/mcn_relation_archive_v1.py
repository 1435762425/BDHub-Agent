"""建立独立的 MCN 官方关系档案与状态观察表。"""
from __future__ import annotations

from sqlalchemy.engine import Engine

from ..schema import mcn_creator_relation, mcn_relation_observation


def apply_mcn_relation_archive_v1(engine: Engine) -> None:
    mcn_creator_relation.create(engine, checkfirst=True)
    mcn_relation_observation.create(engine, checkfirst=True)


__all__ = ["apply_mcn_relation_archive_v1"]
