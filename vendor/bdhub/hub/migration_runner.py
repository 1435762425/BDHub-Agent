"""显式数据库迁移与启动期只读版本校验。

旧版 ``init_db`` 会在业务进程启动时隐式执行建表和一串幂等 DDL。这里把同一
组动作按顺序登记到版本表，生产入口只调用 :func:`check_schema`；真正升级由
``python -m bdhub.migrate --upgrade`` 显式执行。
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

SCHEMA_VERSION_TABLE = "bdhub_schema_version"
MIGRATION_LOCK_KEY = 73100802

MigrationAction = Callable[[Engine], None]
MigrationStep = tuple[int, str, MigrationAction]


class SchemaVersionError(RuntimeError):
    """数据库版本缺失、过旧、过新或出现断档。"""


@dataclass(frozen=True)
class SchemaState:
    """当前数据库版本及已记录的迁移名称。"""

    version: int
    names: tuple[str, ...]


def _migration_steps() -> tuple[MigrationStep, ...]:
    """返回当前代码认识的不可变迁移步骤。

    延迟导入 ``engine`` 是为了避免 engine 工厂与版本运行器互相导入。
    每一步都复用现有幂等动作；以后新增迁移只追加版本，不修改已发布步骤。
    """
    from . import engine as engine_module

    return (
        (1, "metadata", lambda eng: engine_module.metadata.create_all(eng)),
        (2, "report_analysis_v1", engine_module.apply_report_analysis_v1),
        (3, "report_analysis_v2", engine_module.apply_report_analysis_v2),
        (4, "report_analysis_v3", engine_module.apply_report_analysis_v3),
        (5, "report_analysis_v4", engine_module.apply_report_analysis_v4),
        (6, "report_analysis_v5", engine_module.apply_report_analysis_v5),
        (7, "report_analysis_v6", engine_module.apply_report_analysis_v6),
        (8, "report_analysis_v7", engine_module.apply_report_analysis_v7),
        (9, "im_operations_v1", engine_module.apply_im_operations_v1),
        (10, "im_operations_v2", engine_module.apply_im_operations_v2),
        (11, "im_operations_v3", engine_module.apply_im_operations_v3),
        (12, "im_operations_v4", engine_module.apply_im_operations_v4),
        (13, "im_operations_v5", engine_module.apply_im_operations_v5),
        (14, "im_operations_v6", engine_module.apply_im_operations_v6),
        (15, "im_operations_v7", engine_module.apply_im_operations_v7),
        (
            16,
            "inline_compatibility",
            engine_module._apply_inline_compatibility,
        ),
        (17, "mx_core_v1_schema", engine_module.apply_mx_core_v1_schema),
        (18, "mx_core_v1_data", engine_module.apply_mx_core_v1_data),
        (
            19,
            "retire_available_product_pool",
            engine_module.apply_retire_available_product_pool,
        ),
        (20, "mx_only_data_cleanup", engine_module.apply_mx_only_data_cleanup),
        (21, "retire_legacy_tables", engine_module.apply_retire_legacy_tables),
        (
            22,
            "creator_classification_multilabel_v1",
            engine_module.apply_creator_classification_multilabel_v1,
        ),
        (23, "gpm_split_v1", engine_module.apply_gpm_split_v1),
        (
            24,
            "retire_relationship_status_v1",
            engine_module.apply_retire_relationship_status_v1,
        ),
        (
            25,
            "mcn_relation_archive_v1",
            engine_module.apply_mcn_relation_archive_v1,
        ),
        (
            26,
            "mcn_relation_sync_summary_v1",
            engine_module.apply_mcn_relation_sync_summary_v1,
        ),
        (27, "im_operations_v8", engine_module.apply_im_operations_v8),
        (28, "im_operations_v9", engine_module.apply_im_operations_v9),
        (29, "im_operations_v10", engine_module.apply_im_operations_v10),
        (
            30,
            "im_identity_handle_v1",
            engine_module.apply_im_identity_handle_v1,
        ),
        (
            31,
            "contact_hint_semantics_v1",
            engine_module.apply_contact_hint_semantics_v1,
        ),
        (
            32,
            "contact_current_integrity_v1",
            engine_module.apply_contact_current_integrity_v1,
        ),
        (
            33,
            "send_task_multi_market_v1",
            engine_module.apply_send_task_multi_market_v1,
        ),
        (
            34,
            "im_history_recovery_v1",
            engine_module.apply_im_history_recovery_v1,
        ),
        (
            35,
            "share_link_v1",
            engine_module.apply_share_link_v1,
        ),
        (
            36,
            "share_link_product_v1",
            engine_module.apply_share_link_product_v1,
        ),
        (
            37,
            "share_link_record_v1",
            engine_module.apply_share_link_record_v1,
        ),
        (
            38,
            "public_share_link_v1",
            engine_module.apply_public_share_link_v1,
        ),
        (
            39,
            "public_share_link_country_v1",
            engine_module.apply_public_share_link_country_v1,
        ),
        (
            40,
            "merchant_management_v1",
            engine_module.apply_merchant_management_v1,
        ),
        (
            41,
            "sample_management_v1",
            engine_module.apply_sample_management_v1,
        ),
        (
            42,
            "sample_product_image_v1",
            engine_module.apply_sample_product_image_v1,
        ),
        (
            43,
            "sample_workflow_scope_v1",
            engine_module.apply_sample_workflow_scope_v1,
        ),
        (
            44,
            "sample_workflow_import_v1",
            engine_module.apply_sample_workflow_import_v1,
        ),
        (
            45,
            "sample_review_batch_v1",
            engine_module.apply_sample_review_batch_v1,
        ),
        (
            46,
            "product_catalog_version_v1",
            engine_module.apply_product_catalog_version_v1,
        ),
        (
            47,
            "website_catalog_release_v1",
            engine_module.apply_website_catalog_release_v1,
        ),
        (
            48,
            "product_source_pool_v1",
            engine_module.apply_product_source_pool_v1,
        ),
        (
            49,
            "product_source_detail_v1",
            engine_module.apply_product_source_detail_v1,
        ),
        (
            50,
            "product_pool_qualification_v1",
            engine_module.apply_product_pool_qualification_v1,
        ),
        (
            51,
            "product_category_normalization_v1",
            engine_module.apply_product_category_normalization_v1,
        ),
        (
            52,
            "product_source_append_v1",
            engine_module.apply_product_source_append_v1,
        ),
        (
            53,
            "promotion_site_workflow_v1",
            engine_module.apply_promotion_site_workflow_v1,
        ),
        (
            54,
            "promotion_site_sharelink_batch_v1",
            engine_module.apply_promotion_site_sharelink_batch_v1,
        ),
        (
            55,
            "promotion_site_sample_request_v1",
            engine_module.apply_promotion_site_sample_request_v1,
        ),
        (
            56,
            "promotion_site_sample_request_v2",
            engine_module.apply_promotion_site_sample_request_v2,
        ),
        (
            57,
            "promotion_site_category_v1",
            engine_module.apply_promotion_site_category_v1,
        ),
        (
            58,
            "campaign_ai_category_v1",
            engine_module.apply_campaign_ai_category_v1,
        ),
        (
            59,
            "sample_workspace_simplification_v1",
            engine_module.apply_sample_workspace_simplification_v1,
        ),
        (
            60,
            "sample_lifecycle_status_v1",
            engine_module.apply_sample_lifecycle_status_v1,
        ),
        (
            61,
            "sample_review_queue_v1",
            engine_module.apply_sample_review_queue_v1,
        ),
        (
            62,
            "promotion_site_sharelink_parallel_v1",
            engine_module.apply_promotion_site_sharelink_parallel_v1,
        ),
        (63, "im_delivery_intent_v1", engine_module.apply_im_delivery_intent_v1),
        (64, "ai_operations_v1", engine_module.apply_ai_operations_v1),
    )


def _expected_version(steps: tuple[MigrationStep, ...]) -> int:
    if not steps:
        raise SchemaVersionError("当前代码没有可执行的数据库迁移")
    versions = [version for version, _name, _action in steps]
    if versions != list(range(1, len(versions) + 1)):
        raise SchemaVersionError("迁移版本必须从 1 连续递增")
    return versions[-1]


def _ensure_version_table(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                f"create table if not exists {SCHEMA_VERSION_TABLE} ("
                "version integer primary key, "
                "name text not null, "
                "applied_at timestamptz not null default current_timestamp"
                ")"
            )
        )


def _read_state(engine: Engine, steps: tuple[MigrationStep, ...]) -> SchemaState:
    if not inspect(engine).has_table(SCHEMA_VERSION_TABLE):
        raise SchemaVersionError(
            "数据库尚未建立迁移版本表；请先执行 "
            "python -m bdhub.migrate --upgrade"
        )

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"select version, name from {SCHEMA_VERSION_TABLE} "
                "order by version"
            )
        ).all()

    if not rows:
        raise SchemaVersionError(
            "数据库迁移版本表为空；请先执行 python -m bdhub.migrate --upgrade"
        )

    versions = [int(row[0]) for row in rows]
    if versions != list(range(1, versions[-1] + 1)):
        raise SchemaVersionError("数据库迁移版本存在断档，拒绝继续启动")

    expected_names = {
        version: name for version, name, _action in steps
    }
    for version, name in rows:
        if version not in expected_names:
            raise SchemaVersionError(
                f"数据库版本 {version} 高于当前代码，当前代码无法安全启动"
            )
        if name != expected_names[version]:
            raise SchemaVersionError(
                f"迁移版本 {version} 名称不匹配：数据库={name!r}，"
                f"代码={expected_names[version]!r}"
            )

    return SchemaState(
        version=versions[-1],
        names=tuple(str(row[1]) for row in rows),
    )


def _check_expected(state: SchemaState, expected: int) -> SchemaState:
    if state.version != expected:
        direction = "过旧" if state.version < expected else "过新"
        raise SchemaVersionError(
            f"数据库迁移版本{direction}：当前={state.version}，"
            f"要求={expected}；请先执行 python -m bdhub.migrate --upgrade"
        )
    return state


@contextmanager
def _migration_lock(engine: Engine) -> Iterator[None]:
    """PostgreSQL 上串行化升级；非 PG 测试库保持无额外依赖。"""
    if engine.dialect.name != "postgresql":
        yield
        return

    connection = engine.connect()
    try:
        connection.execute(
            text("select pg_advisory_lock(:lock_key)"),
            {"lock_key": MIGRATION_LOCK_KEY},
        )
        yield
    finally:
        try:
            connection.execute(
                text("select pg_advisory_unlock(:lock_key)"),
                {"lock_key": MIGRATION_LOCK_KEY},
            )
        finally:
            connection.close()


def check_schema(
    engine: Engine,
    *,
    minimum_version: int | None = None,
) -> SchemaState:
    """只读校验数据库版本；可为独立只读能力声明最低兼容版本。"""
    steps = _migration_steps()
    state = _read_state(engine, steps)
    expected = _expected_version(steps)
    if minimum_version is None:
        return _check_expected(state, expected)
    required = int(minimum_version)
    if required <= 0 or required > expected:
        raise ValueError("minimum schema version 非法")
    if state.version < required:
        raise SchemaVersionError(
            f"数据库迁移版本过旧：当前={state.version}，"
            f"此能力最低要求={required}；请先执行 python -m bdhub.migrate --upgrade"
        )
    return state


def upgrade_schema(engine: Engine) -> int:
    """显式执行缺失迁移，并逐步写入版本表。"""
    steps = _migration_steps()
    expected = _expected_version(steps)

    with _migration_lock(engine):
        _ensure_version_table(engine)
        try:
            state = _read_state(engine, steps)
        except SchemaVersionError as exc:
            if "尚未建立迁移版本表" in str(exc):
                raise
            # 空表允许从第一个迁移开始；断档、名称不匹配和未来版本仍拒绝。
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        f"select version, name from {SCHEMA_VERSION_TABLE} "
                        "order by version"
                    )
                ).all()
            if rows:
                raise
            state = SchemaState(version=0, names=())

        if state.version > expected:
            raise SchemaVersionError(
                f"数据库版本 {state.version} 高于当前代码版本 {expected}"
            )

        for version, name, action in steps[state.version:]:
            action(engine)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        f"insert into {SCHEMA_VERSION_TABLE} "
                        "(version, name) values (:version, :name) "
                        "on conflict (version) do nothing"
                    ),
                    {"version": version, "name": name},
                )

    return expected


__all__ = [
    "MIGRATION_LOCK_KEY",
    "SCHEMA_VERSION_TABLE",
    "SchemaState",
    "SchemaVersionError",
    "check_schema",
    "upgrade_schema",
]
