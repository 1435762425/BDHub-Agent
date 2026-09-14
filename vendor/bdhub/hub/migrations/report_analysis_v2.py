"""报告分析 v2 expand 迁移：只补 Phase B 查询索引。"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine


# 表名、索引名和 DDL 均为代码内固定白名单，不接受运行时标识符输入。
_INDEX_MIGRATIONS = (
    (
        "partner_attribution_daily",
        "ix_partner_attribution_daily_analysis_identity",
        "drop index concurrently if exists "
        "ix_partner_attribution_daily_analysis_identity",
        "create index concurrently if not exists "
        "ix_partner_attribution_daily_analysis_identity "
        "on partner_attribution_daily "
        "(market, report_kind, creator_identity_key, report_date)",
    ),
    (
        "creator_classification_assignment",
        "ix_creator_classification_assignment_lookup",
        "drop index concurrently if exists "
        "ix_creator_classification_assignment_lookup",
        "create index concurrently if not exists "
        "ix_creator_classification_assignment_lookup "
        "on creator_classification_assignment "
        "(market, classification_id, identity_key)",
    ),
    (
        "report_creator_identity",
        "ix_report_creator_identity_classifiable_key",
        "drop index concurrently if exists "
        "ix_report_creator_identity_classifiable_key",
        "create index concurrently if not exists "
        "ix_report_creator_identity_classifiable_key "
        "on report_creator_identity "
        "(market, classifiable, identity_key)",
    ),
)
_INDEX_TARGETS = frozenset(
    (table_name, index_name)
    for table_name, index_name, _drop_statement, _create_statement
    in _INDEX_MIGRATIONS
)
_MIGRATION_LOCK_ID = 2_026_072_600_000_002
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
    target = (table_name, index_name)
    if target not in _INDEX_TARGETS:
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
        # 获取结果可能处于“服务端成功、客户端未确认”的模糊态；
        # 销毁物理连接才能保证它不会带着 session 锁返回连接池。
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
                "report analysis migration advisory lock did not release"
            )
    except BaseException:
        # session advisory lock 不随 rollback 释放；解锁失败时必须
        # 丢弃物理 session，不能让 SQLAlchemy 将其归还连接池。
        connection.invalidate()
        raise


def apply_report_analysis_v2(engine: Engine) -> None:
    """幂等补齐查询索引；非 PostgreSQL 后端安全跳过。"""
    if engine.dialect.name != "postgresql":
        return

    with engine.connect().execution_options(
        isolation_level="AUTOCOMMIT",
    ) as connection:
        # session 锁、状态探测和并发 DDL 必须共用同一物理连接。
        lock_parameters = {"lock_id": _MIGRATION_LOCK_ID}
        _acquire_migration_lock(connection, lock_parameters)
        try:
            inspector = inspect(connection)
            index_names_by_table = {
                table_name: {
                    item["name"]
                    for item in inspector.get_indexes(table_name)
                }
                for (
                    table_name,
                    _index_name,
                    _drop_statement,
                    _create_statement,
                )
                in _INDEX_MIGRATIONS
            }
            repair_steps = []
            for (
                table_name,
                index_name,
                drop_statement,
                create_statement,
            ) in _INDEX_MIGRATIONS:
                if index_name not in index_names_by_table[table_name]:
                    repair_steps.append((None, create_statement))
                    continue
                if _index_is_valid(
                    connection,
                    table_name=table_name,
                    index_name=index_name,
                ) is not True:
                    repair_steps.append(
                        (drop_statement, create_statement)
                    )

            for drop_statement, create_statement in repair_steps:
                if drop_statement is not None:
                    connection.execute(text(drop_statement))
                connection.execute(text(create_statement))
        finally:
            _release_migration_lock(connection, lock_parameters)
