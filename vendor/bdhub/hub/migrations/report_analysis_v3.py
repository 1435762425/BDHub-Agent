"""报告分析 v3：为日聚合补精确非空事实行计数。"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


_TABLES = (
    "report_creator_daily",
    "report_product_daily",
    "report_shop_daily",
    "report_campaign_daily",
)
_DDL = {
    table_name: text(
        f"alter table {table_name} "
        "add column if not exists present_row_count bigint"
    )
    for table_name in _TABLES
}


def apply_report_analysis_v3(engine: Engine) -> None:
    """只补轻量 nullable 列；历史数据由显式 aggregate refresh 回填。"""
    if engine.dialect.name != "postgresql":
        return

    inspector = inspect(engine)
    missing_tables = [
        table_name
        for table_name in _TABLES
        if "present_row_count" not in {
            column["name"]
            for column in inspector.get_columns(table_name)
        }
    ]
    if not missing_tables:
        return

    with engine.begin() as connection:
        for table_name in missing_tables:
            connection.execute(_DDL[table_name])


__all__ = ["apply_report_analysis_v3"]
