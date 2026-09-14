"""报告分析 v1 expand 迁移：只补 nullable 列和并发索引。"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


_COLUMN_MIGRATIONS = (
    (
        "partner_attribution_daily",
        "creator_identity_key",
        "alter table partner_attribution_daily "
        "add column if not exists creator_identity_key text",
    ),
    (
        "creator_report_observation",
        "candidate_oec_ids",
        "alter table creator_report_observation "
        "add column if not exists candidate_oec_ids jsonb "
        "not null default '[]'::jsonb",
    ),
    *(
        (
            table_name,
            column_name,
            f"alter table {table_name} add column if not exists "
            f"{column_name} numeric",
        )
        for table_name in (
            "report_creator_daily",
            "report_product_daily",
            "report_shop_daily",
            "report_campaign_daily",
        )
        for column_name in (
            "estimated_partner_commission",
            "actual_partner_commission",
        )
    ),
)

_IDENTITY_INDEX = "ix_partner_attribution_identity"
_IDENTITY_INDEX_DDL = (
    "create index concurrently if not exists "
    f"{_IDENTITY_INDEX} on partner_attribution_daily "
    "(market, report_kind, report_date, creator_identity_key)"
)
_DEFINITION_EVENT_CONSTRAINT = (
    "ck_creator_classification_definition_event_type"
)
_DEFINITION_EVENT_CONSTRAINT_DDL = (
    "alter table creator_classification_definition_event "
    f"drop constraint if exists {_DEFINITION_EVENT_CONSTRAINT}; "
    "alter table creator_classification_definition_event "
    f"add constraint {_DEFINITION_EVENT_CONSTRAINT} check ("
    "event_type in ("
    "'create','rename','recolor','reorder','activate',"
    "'deactivate','delete','update'"
    "))"
)


def apply_report_analysis_v1(engine: Engine) -> None:
    """安全重复执行报告分析 expand DDL，不在启动时做数据回填。"""
    with engine.connect() as connection:
        inspector = inspect(connection)
        table_names = {
            table_name
            for table_name, _column_name, _statement
            in _COLUMN_MIGRATIONS
        }
        columns_by_table = {
            table_name: {
                item["name"]
                for item in inspector.get_columns(table_name)
            }
            for table_name in table_names
        }
        index_names = {
            item["name"]
            for item in inspector.get_indexes(
                "partner_attribution_daily"
            )
        }
        definition_event_constraints = (
            inspector.get_check_constraints(
                "creator_classification_definition_event"
            )
        )
        definition_event_supports_update = any(
            item.get("name") == _DEFINITION_EVENT_CONSTRAINT
            and "'update'" in str(item.get("sqltext") or "").lower()
            for item in definition_event_constraints
        )

    missing_columns = [
        statement
        for table_name, column_name, statement
        in _COLUMN_MIGRATIONS
        if column_name not in columns_by_table[table_name]
    ]
    if missing_columns:
        with engine.begin() as connection:
            for statement in missing_columns:
                connection.execute(text(statement))
    if not definition_event_supports_update:
        with engine.begin() as connection:
            connection.execute(
                text(_DEFINITION_EVENT_CONSTRAINT_DDL)
            )
    if _IDENTITY_INDEX not in index_names:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT",
        ) as connection:
            connection.execute(text(_IDENTITY_INDEX_DDL))
