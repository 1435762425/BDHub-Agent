"""报告分析 v4：新增分类对象排行窄型日聚合表。"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine


_TABLE_NAME = "report_classification_object_daily"
_CREATE_TABLE_DDL = text("""
    create table if not exists report_classification_object_daily (
        market text not null,
        report_kind text not null,
        report_date date not null,
        dimension text not null,
        identity_key text not null,
        dimension_key text not null,
        label text,
        row_count bigint not null,
        gmv numeric,
        orders bigint,
        refreshed_at timestamptz not null,
        primary key (
            market,
            report_kind,
            report_date,
            dimension,
            identity_key,
            dimension_key
        ),
        constraint ck_report_classification_object_daily_dimension
            check (dimension in ('product','shop','campaign')),
        constraint ck_report_classification_object_daily_kind
            check (report_kind in ('mcn','tap')),
        constraint ck_report_classification_object_daily_campaign
            check (report_kind = 'tap' or dimension != 'campaign')
    )
""")
_INDEX_COLUMNS = {
    "ix_report_classification_object_daily_identity": (
        "market, report_kind, dimension, identity_key, "
        "report_date, dimension_key"
    ),
    "ix_report_classification_object_daily_object": (
        "market, report_kind, dimension, dimension_key, "
        "report_date, identity_key"
    ),
}
_CREATE_INDEX_CONCURRENTLY_DDL = {
    index_name: text(
        "create index concurrently if not exists "
        f"{index_name} on {_TABLE_NAME} ({columns})"
    )
    for index_name, columns in _INDEX_COLUMNS.items()
}
_DROP_INDEX_CONCURRENTLY_DDL = {
    index_name: text(
        f"drop index concurrently if exists {index_name}"
    )
    for index_name in _INDEX_COLUMNS
}
_INDEX_TARGETS = frozenset(
    (_TABLE_NAME, index_name)
    for index_name in _INDEX_COLUMNS
)
_MIGRATION_LOCK_ID = 2_026_072_600_000_004
_ADVISORY_LOCK_SQL = text(
    "select pg_catalog.pg_advisory_lock(:lock_id)"
)
_ADVISORY_UNLOCK_SQL = text(
    "select pg_catalog.pg_advisory_unlock(:lock_id)"
)
_INDEX_VALIDITY_SQL = text("""
    select index_state.indisvalid
    from pg_catalog.pg_index as index_state
    join pg_catalog.pg_class as index_relation
      on index_relation.oid = index_state.indexrelid
    join pg_catalog.pg_class as table_relation
      on table_relation.oid = index_state.indrelid
    join pg_catalog.pg_namespace as table_namespace
      on table_namespace.oid = table_relation.relnamespace
    where table_namespace.nspname = current_schema()
      and index_relation.relnamespace = table_namespace.oid
      and table_relation.relname = :table_name
      and index_relation.relname = :index_name
""")


def _index_is_valid(
    connection: Connection,
    *,
    table_name: str,
    index_name: str,
) -> bool | None:
    if (table_name, index_name) not in _INDEX_TARGETS:
        raise ValueError("index validity probe target is not allowlisted")
    return connection.execute(
        _INDEX_VALIDITY_SQL,
        {
            "table_name": table_name,
            "index_name": index_name,
        },
    ).scalar_one_or_none()


def _acquire_migration_lock(
    connection: Connection,
    parameters: dict[str, int],
) -> None:
    try:
        connection.execute(
            _ADVISORY_LOCK_SQL,
            parameters,
        ).scalar_one()
    except BaseException:
        connection.invalidate()
        raise


def _release_migration_lock(
    connection: Connection,
    parameters: dict[str, int],
) -> None:
    try:
        released = connection.execute(
            _ADVISORY_UNLOCK_SQL,
            parameters,
        ).scalar_one()
        if released is not True:
            raise RuntimeError(
                "report analysis v4 migration advisory lock "
                "did not release"
            )
    except BaseException:
        connection.invalidate()
        raise


def _repair_indexes(engine: Engine) -> None:
    with engine.connect().execution_options(
        isolation_level="AUTOCOMMIT",
    ) as connection:
        lock_parameters = {"lock_id": _MIGRATION_LOCK_ID}
        _acquire_migration_lock(connection, lock_parameters)
        try:
            inspector = inspect(connection)
            existing_indexes = {
                item["name"]
                for item in inspector.get_indexes(_TABLE_NAME)
            }
            repair_steps = []
            for index_name in _INDEX_COLUMNS:
                if index_name not in existing_indexes:
                    repair_steps.append((None, index_name))
                    continue
                if _index_is_valid(
                    connection,
                    table_name=_TABLE_NAME,
                    index_name=index_name,
                ) is not True:
                    repair_steps.append((index_name, index_name))

            for drop_index, create_index in repair_steps:
                if drop_index is not None:
                    connection.execute(
                        _DROP_INDEX_CONCURRENTLY_DDL[drop_index]
                    )
                connection.execute(
                    _CREATE_INDEX_CONCURRENTLY_DDL[create_index]
                )
        finally:
            _release_migration_lock(
                connection,
                lock_parameters,
            )


def apply_report_analysis_v4(engine: Engine) -> None:
    """仅扩表，不在应用启动时扫描事实表或回填历史数据。"""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        if not inspect(connection).has_table(_TABLE_NAME):
            connection.execute(_CREATE_TABLE_DDL)
    _repair_indexes(engine)


__all__ = ["apply_report_analysis_v4"]
