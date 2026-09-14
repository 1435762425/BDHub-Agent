"""样品适用达人和官方女装表单的持久预览导入。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_sample_workflow_import_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists sample_workflow_import_batch (
                import_id text primary key,
                bd_market text not null,
                import_type text not null,
                status text not null,
                file_name text not null,
                sha256 text not null,
                row_count bigint not null,
                insert_count bigint not null default 0,
                update_count bigint not null default 0,
                unchanged_count bigint not null default 0,
                remove_count bigint not null default 0,
                conflict_count bigint not null default 0,
                error_count bigint not null default 0,
                import_metadata jsonb not null default '{}'::jsonb,
                created_by text not null,
                created_at timestamptz not null,
                applied_at timestamptz,
                result_summary jsonb,
                constraint ck_sample_workflow_import_market check (bd_market = 'mx'),
                constraint ck_sample_workflow_import_type check (
                    import_type in ('creator_eligibility','official_product_form')
                ),
                constraint ck_sample_workflow_import_status check (
                    status in ('previewed','applying','applied','expired','failed')
                ),
                constraint ck_sample_workflow_import_counts check (
                    row_count >= 0 and insert_count >= 0 and update_count >= 0
                    and unchanged_count >= 0 and remove_count >= 0
                    and conflict_count >= 0 and error_count >= 0
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_workflow_import_type_time
                on sample_workflow_import_batch (
                    bd_market, import_type, created_at
                )
        """))
        connection.execute(text("""
            create table if not exists sample_workflow_import_row (
                import_id text not null,
                row_number bigint not null,
                status text not null,
                entity_key text,
                payload jsonb not null default '{}'::jsonb,
                current_payload jsonb,
                error_code text,
                primary key (import_id, row_number),
                constraint fk_sample_workflow_import_row_batch
                    foreign key (import_id)
                    references sample_workflow_import_batch(import_id),
                constraint ck_sample_workflow_import_row_status check (
                    status in (
                        'insert','update','unchanged','remove','conflict','invalid'
                    )
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_workflow_import_row_status
                on sample_workflow_import_row (import_id, status, row_number)
        """))


__all__ = ["apply_sample_workflow_import_v1"]
