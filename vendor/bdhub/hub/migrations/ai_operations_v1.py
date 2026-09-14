"""可选 schema v64：持久分发计划、类目初筛与人工合作事项。"""
from sqlalchemy.engine import Engine


def apply_ai_operations_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise ValueError("ai_operations_requires_postgresql")
    from ..schema import AI_OPERATIONS_TABLES, metadata
    metadata.create_all(engine, tables=list(AI_OPERATIONS_TABLES), checkfirst=True)
