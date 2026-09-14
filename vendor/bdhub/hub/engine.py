# -*- coding: utf-8 -*-
"""PostgreSQL engine 工厂与数据库入口。

业务进程只调用 ``check_schema`` 做只读版本校验；``upgrade_schema`` 由显式迁移
命令调用。``init_db`` 保留给旧测试/脚本兼容，不再被生产入口使用。
（原属 bdhub/db.py，2026-07-06 拆分迁移；schema 定义见 bdhub/hub/schema.py。）
"""
from __future__ import annotations

from sqlalchemy import create_engine, text as _sqltext
from sqlalchemy.engine import Engine

from .. import config as _config
from .migrations import (
    apply_ai_operations_v1,
    apply_report_analysis_v1,
    apply_report_analysis_v2,
    apply_report_analysis_v3,
    apply_report_analysis_v4,
    apply_report_analysis_v5,
    apply_report_analysis_v6,
    apply_report_analysis_v7,
    apply_im_operations_v1,
    apply_im_operations_v2,
    apply_im_operations_v3,
    apply_im_operations_v4,
    apply_im_operations_v5,
    apply_im_operations_v6,
    apply_im_operations_v7,
    apply_im_operations_v8,
    apply_im_operations_v9,
    apply_im_operations_v10,
    apply_im_identity_handle_v1,
    apply_im_history_recovery_v1,
    apply_mx_core_v1_schema,
    apply_mx_core_v1_data,
    apply_retire_available_product_pool,
    apply_mx_only_data_cleanup,
    apply_retire_legacy_tables,
    apply_creator_classification_multilabel_v1,
    apply_gpm_split_v1,
    apply_retire_relationship_status_v1,
    apply_mcn_relation_archive_v1,
    apply_mcn_relation_sync_summary_v1,
    apply_contact_hint_semantics_v1,
    apply_contact_current_integrity_v1,
    apply_send_task_multi_market_v1,
    apply_share_link_v1,
    apply_share_link_product_v1,
    apply_share_link_record_v1,
    apply_public_share_link_v1,
    apply_public_share_link_country_v1,
    apply_merchant_management_v1,
    apply_sample_management_v1,
    apply_sample_product_image_v1,
    apply_sample_workflow_scope_v1,
    apply_sample_workflow_import_v1,
    apply_sample_review_batch_v1,
    apply_product_catalog_version_v1,
    apply_website_catalog_release_v1,
    apply_product_source_pool_v1,
    apply_product_source_detail_v1,
    apply_product_pool_qualification_v1,
    apply_product_category_normalization_v1,
    apply_product_source_append_v1,
    apply_promotion_site_workflow_v1,
    apply_promotion_site_sharelink_batch_v1,
    apply_promotion_site_sample_request_v1,
    apply_promotion_site_sample_request_v2,
    apply_promotion_site_category_v1,
    apply_campaign_ai_category_v1,
    apply_sample_workspace_simplification_v1,
    apply_sample_lifecycle_status_v1,
    apply_sample_review_queue_v1,
    apply_im_delivery_intent_v1,
    apply_promotion_site_sharelink_parallel_v1,
)
from .schema import metadata

# ---------------- engine（进程内单例 + 连接池） ----------------
_ENGINE: Engine | None = None
RUNTIME_MINIMUM_SCHEMA_VERSION = 63


def make_engine(
    cfg=None,
    *,
    pool_timeout_seconds: int | None = None,
    connect_timeout_seconds: int | None = None,
) -> Engine:
    """新建一个 engine（不缓存）。测试或需要独立连接池时用。"""
    cfg = cfg or _config.load()
    engine_options: dict[str, object] = {
        "pool_size": cfg.db_pool_size,
        "max_overflow": cfg.db_max_overflow,
        "pool_pre_ping": True,
        "future": True,
    }
    if pool_timeout_seconds is not None:
        engine_options["pool_timeout"] = pool_timeout_seconds
    if connect_timeout_seconds is not None:
        engine_options["connect_args"] = {
            "connect_timeout": connect_timeout_seconds,
        }
    return create_engine(
        cfg.db_url,
        **engine_options,
    )


def get_engine(cfg=None) -> Engine:
    """进程内共享 engine。多线程安全（连接池），enrich 并发 worker 复用同一池。"""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = make_engine(cfg)
    return _ENGINE


def reset_engine() -> None:
    """释放共享 engine（测试/重连场景）。"""
    global _ENGINE
    if _ENGINE is not None:
        _ENGINE.dispose()
        _ENGINE = None


def _apply_inline_compatibility(engine: Engine) -> None:
    """应用历史上散落在 ``init_db`` 尾部的兼容 DDL。"""
    with engine.begin() as connection:
        connection.execute(_sqltext("""
            create or replace view partner_attribution_current as
            select fact.*
            from partner_attribution_daily fact
            join report_file file on file.file_id = fact.file_id
            where file.status = 'sealed'
              and file.is_current is true
        """))
        connection.execute(_sqltext(
            "alter table im_auto_reply add column if not exists objection_count integer default 0"))
        connection.execute(_sqltext(
            "alter table im_auto_reply add column if not exists link_sent_at timestamptz"))
        connection.execute(_sqltext(
            "create index if not exists ix_cp_oecid on creator_profile (oec_id, bd_market)"))
        connection.execute(_sqltext(
            "alter table im_auto_reply add column if not exists wa_added_at timestamptz"))
        connection.execute(_sqltext(
            "alter table im_auto_reply add column if not exists handoff_done_at timestamptz"))


def init_db(engine: Engine | None = None) -> None:
    """兼容旧测试/脚本的直接建表入口；生产启动不得调用。"""
    eng = engine or get_engine()
    metadata.create_all(eng)
    apply_report_analysis_v1(eng)
    apply_report_analysis_v2(eng)
    apply_report_analysis_v3(eng)
    apply_report_analysis_v4(eng)
    apply_report_analysis_v5(eng)
    apply_report_analysis_v6(eng)
    apply_report_analysis_v7(eng)
    apply_im_operations_v1(eng)
    apply_im_operations_v2(eng)
    apply_im_operations_v3(eng)
    apply_im_operations_v4(eng)
    apply_im_operations_v5(eng)
    apply_im_operations_v6(eng)
    apply_im_operations_v7(eng)
    apply_im_operations_v8(eng)
    apply_im_operations_v9(eng)
    apply_im_operations_v10(eng)
    apply_im_identity_handle_v1(eng)
    apply_mx_core_v1_schema(eng)
    apply_mx_core_v1_data(eng)
    apply_retire_available_product_pool(eng)
    apply_mx_only_data_cleanup(eng)
    apply_retire_legacy_tables(eng)
    apply_creator_classification_multilabel_v1(eng)
    apply_gpm_split_v1(eng)
    _apply_inline_compatibility(eng)
    apply_retire_relationship_status_v1(eng)
    apply_mcn_relation_archive_v1(eng)
    apply_mcn_relation_sync_summary_v1(eng)
    apply_contact_hint_semantics_v1(eng)
    apply_contact_current_integrity_v1(eng)
    apply_send_task_multi_market_v1(eng)
    apply_im_history_recovery_v1(eng)
    apply_share_link_v1(eng)
    apply_share_link_product_v1(eng)
    apply_share_link_record_v1(eng)
    apply_public_share_link_v1(eng)
    apply_public_share_link_country_v1(eng)
    apply_merchant_management_v1(eng)
    apply_sample_management_v1(eng)
    apply_sample_product_image_v1(eng)
    apply_sample_workflow_scope_v1(eng)
    apply_sample_workflow_import_v1(eng)
    apply_sample_review_batch_v1(eng)
    apply_product_catalog_version_v1(eng)
    apply_website_catalog_release_v1(eng)
    apply_product_source_pool_v1(eng)
    apply_product_source_detail_v1(eng)
    apply_product_pool_qualification_v1(eng)
    apply_product_category_normalization_v1(eng)
    apply_product_source_append_v1(eng)
    apply_promotion_site_workflow_v1(eng)
    apply_promotion_site_sharelink_batch_v1(eng)
    apply_promotion_site_sample_request_v1(eng)
    apply_promotion_site_sample_request_v2(eng)
    apply_promotion_site_category_v1(eng)
    apply_campaign_ai_category_v1(eng)
    apply_sample_workspace_simplification_v1(eng)
    apply_sample_lifecycle_status_v1(eng)
    apply_sample_review_queue_v1(eng)
    apply_promotion_site_sharelink_parallel_v1(eng)


def check_schema(
    engine: Engine | None = None,
    *,
    minimum_version: int | None = RUNTIME_MINIMUM_SCHEMA_VERSION,
):
    """生产运行只读校验；默认要求当前运行基线，不隐式执行 DDL。

    需要核对仓库全部迁移是否已应用时显式传 ``minimum_version=None``。
    独立能力若依赖更高版本，必须声明自己的最低版本。
    """
    from .migration_runner import check_schema as _check_schema

    return _check_schema(
        engine or get_engine(),
        minimum_version=minimum_version,
    )


def upgrade_schema(engine: Engine | None = None) -> int:
    """显式升级数据库版本；业务启动路径不得调用此函数。"""
    from .migration_runner import upgrade_schema as _upgrade_schema

    return _upgrade_schema(engine or get_engine())
