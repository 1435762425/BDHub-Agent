"""记录 relation/list 官方计数与可下发实体数之间的差异。"""
from __future__ import annotations

from sqlalchemy.engine import Engine

from ..schema import mcn_relation_sync_run


def apply_mcn_relation_sync_summary_v1(engine: Engine) -> None:
    mcn_relation_sync_run.create(engine, checkfirst=True)


__all__ = ["apply_mcn_relation_sync_summary_v1"]
