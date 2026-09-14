"""样品批量批准预览、同步复核和串行执行审计。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_sample_review_batch_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists sample_review_batch (
                batch_id text primary key,
                bd_market text not null,
                status text not null,
                selected_count bigint not null,
                ready_count bigint not null,
                excluded_count bigint not null,
                succeeded_count bigint not null default 0,
                failed_count bigint not null default 0,
                result_unknown_count bigint not null default 0,
                preview_snapshot jsonb not null,
                sync_run_id text,
                account_name text,
                created_by text not null,
                created_at timestamptz not null,
                started_at timestamptz,
                finished_at timestamptz,
                error_code text,
                constraint fk_sample_review_batch_sync foreign key (sync_run_id)
                    references sample_sync_run(run_id),
                constraint ck_sample_review_batch_market check (bd_market = 'mx'),
                constraint ck_sample_review_batch_status check (
                    status in (
                        'previewed','syncing','ready','running','completed',
                        'completed_with_errors','result_unknown','failed','expired'
                    )
                ),
                constraint ck_sample_review_batch_counts check (
                    selected_count >= 1 and ready_count >= 0
                    and excluded_count >= 0 and succeeded_count >= 0
                    and failed_count >= 0 and result_unknown_count >= 0
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_review_batch_status
                on sample_review_batch (bd_market, status, created_at)
        """))
        connection.execute(text("""
            create table if not exists sample_review_batch_item (
                batch_id text not null,
                request_id text not null,
                item_order bigint not null,
                status text not null,
                blocking_reason text,
                attempt_id text,
                started_at timestamptz,
                finished_at timestamptz,
                primary key (batch_id, request_id),
                constraint fk_sample_review_batch_item_batch foreign key (batch_id)
                    references sample_review_batch(batch_id),
                constraint fk_sample_review_batch_item_request foreign key (request_id)
                    references sample_request_current(request_id),
                constraint fk_sample_review_batch_item_attempt foreign key (attempt_id)
                    references sample_review_attempt(attempt_id),
                constraint ck_sample_review_batch_item_status check (
                    status in (
                        'ready','excluded','running','succeeded','failed',
                        'result_unknown','cancelled'
                    )
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_review_batch_item_status
                on sample_review_batch_item (batch_id, status, item_order)
        """))


__all__ = ["apply_sample_review_batch_v1"]
