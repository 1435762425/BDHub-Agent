"""MX 高佣 ShareLink durable job、工作项、产物与尝试记录。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_share_link_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists share_link_job (
                job_id text primary key,
                bd_market text not null,
                name text not null,
                commission_mode text not null,
                commission_config jsonb not null default '{}'::jsonb,
                account_name text,
                status text not null,
                phase text not null,
                revision integer not null default 1,
                source_name text,
                source_sha256 text,
                confirmed_snapshot jsonb not null default '{}'::jsonb,
                process_job_id text,
                created_by text not null,
                created_at timestamptz not null,
                updated_at timestamptz not null,
                started_at timestamptz,
                finished_at timestamptz,
                constraint ck_share_link_job_market check (
                    bd_market in ('mx','br','uk','it','jp','us','de')
                ),
                constraint ck_share_link_job_commission_mode check (
                    commission_mode in ('MX_AVERAGE','MAX_CREATOR','KEEP_MARGIN','BOOST_OVER_OPEN')
                ),
                constraint ck_share_link_job_status check (
                    status in ('draft','previewing','ready','running','paused','completed',
                        'completed_with_errors','needs_review','failed','cancelled')
                ),
                constraint ck_share_link_job_phase check (
                    phase in ('import','preview','confirm','generate','result')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_share_link_job_market_status
                on share_link_job (bd_market, status, updated_at)
        """))
        connection.execute(text("""
            create table if not exists share_link_plan (
                plan_record_id text primary key,
                bd_market text not null,
                account_name text,
                partner_identity_fingerprint text not null,
                pid text not null,
                campaign_id text not null,
                creator_commission numeric not null,
                request_fingerprint text not null unique,
                share_url text not null,
                platform_plan_id text not null unique,
                status text not null,
                source text not null,
                created_at timestamptz not null,
                last_verified_at timestamptz,
                constraint ck_share_link_plan_market check (
                    bd_market in ('mx','br','uk','it','jp','us','de')
                ),
                constraint ck_share_link_plan_status check (
                    status in ('active','legacy_imported')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_share_link_plan_pid
                on share_link_plan (bd_market, pid, created_at)
        """))
        connection.execute(text("""
            create table if not exists share_link_item (
                item_id text primary key,
                job_id text not null references share_link_job(job_id),
                source_row_no integer not null,
                pid text not null,
                commission_override numeric,
                input_fingerprint text not null,
                selected_for_generation boolean not null default true,
                search_status text not null,
                create_status text not null,
                candidate_snapshot jsonb not null default '[]'::jsonb,
                selected_campaign_id text,
                selected_campaign_name text,
                product_name text,
                shop_name text,
                campaign_type integer,
                campaign_end_at timestamptz,
                free_sample boolean,
                total_commission numeric,
                open_commission numeric,
                creator_commission numeric,
                agency_margin numeric,
                plan_record_id text references share_link_plan(plan_record_id),
                error_code text,
                error_detail text,
                created_at timestamptz not null,
                updated_at timestamptz not null,
                constraint uq_share_link_item_input unique (job_id, input_fingerprint),
                constraint ck_share_link_item_search_status check (
                    search_status in ('pending','searching','ready','not_found','no_valid_campaign','failed')
                ),
                constraint ck_share_link_item_create_status check (
                    create_status in ('not_requested','queued','creating','succeeded','reused',
                        'result_unknown','failed','cancelled')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_share_link_item_job_status
                on share_link_item (job_id, search_status, create_status)
        """))
        connection.execute(text("""
            create index if not exists ix_share_link_item_pid
                on share_link_item (pid)
        """))
        connection.execute(text("""
            create table if not exists share_link_attempt (
                attempt_id text primary key,
                item_id text not null references share_link_item(item_id),
                phase text not null,
                attempt_no integer not null,
                status text not null,
                account_name text not null,
                request_dispatched_at timestamptz,
                finished_at timestamptz,
                http_status integer,
                platform_code text,
                error_code text,
                error_detail text,
                transport_snapshot jsonb not null default '{}'::jsonb,
                created_at timestamptz not null,
                constraint uq_share_link_attempt_no unique (item_id, phase, attempt_no),
                constraint ck_share_link_attempt_phase check (phase in ('search','create')),
                constraint ck_share_link_attempt_status check (
                    status in ('started','succeeded','failed','result_unknown')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_share_link_attempt_item
                on share_link_attempt (item_id, phase, attempt_no)
        """))


__all__ = ["apply_share_link_v1"]
