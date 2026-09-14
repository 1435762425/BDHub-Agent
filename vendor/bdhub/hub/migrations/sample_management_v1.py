"""推广网站 ShareLink 绑定与样品人工审批工作流。"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine


_POLICY_ROWS = (
    # pool_type, mcn_bound, min_gmv_mxn, max_gmv_mxn, weekly_limit
    ("viral", False, 0, 50_000, 0),
    ("viral", True, 0, 50_000, 1),
    ("potential", False, 0, 50_000, 1),
    ("potential", True, 0, 50_000, 3),
    ("viral", False, 50_000, 100_000, 0),
    ("viral", True, 50_000, 100_000, 2),
    ("potential", False, 50_000, 100_000, 2),
    ("potential", True, 50_000, 100_000, 3),
    ("viral", False, 100_000, 200_000, 2),
    ("viral", True, 100_000, 200_000, 3),
    ("potential", False, 100_000, 200_000, 2),
    ("potential", True, 100_000, 200_000, 4),
    ("viral", False, 200_000, 500_000, 2),
    ("viral", True, 200_000, 500_000, 4),
    ("potential", False, 200_000, 500_000, 3),
    ("potential", True, 200_000, 500_000, 6),
    ("viral", False, 500_000, 1_000_000, 3),
    ("viral", True, 500_000, 1_000_000, 5),
    ("potential", False, 500_000, 1_000_000, 3),
    ("potential", True, 500_000, 1_000_000, 8),
    ("viral", False, 1_000_000, None, 3),
    ("viral", True, 1_000_000, None, 8),
    ("potential", False, 1_000_000, None, 4),
    ("potential", True, 1_000_000, None, 10),
)


def apply_sample_management_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists promotion_sharelink_binding (
                bd_market text not null,
                pid text not null,
                plan_record_id text not null,
                status text not null default 'active',
                source text not null default 'promotion_site',
                created_by text not null,
                updated_by text not null,
                created_at timestamptz not null,
                updated_at timestamptz not null,
                last_verified_at timestamptz,
                primary key (bd_market, pid),
                constraint fk_promotion_sharelink_plan foreign key (plan_record_id)
                    references share_link_plan(plan_record_id),
                constraint ck_promotion_sharelink_market check (
                    bd_market in ('mx','br','uk','it','jp','us','de')
                ),
                constraint ck_promotion_sharelink_pid check (
                    pid ~ '^[0-9]{10,32}$'
                ),
                constraint ck_promotion_sharelink_status check (
                    status in ('active','needs_review','result_unknown','disabled')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_promotion_sharelink_status
                on promotion_sharelink_binding (bd_market, status, updated_at)
        """))
        connection.execute(text("""
            create table if not exists promotion_catalog_product (
                bd_market text not null,
                catalog_key text not null,
                pid text not null,
                seller_id text not null,
                campaign_id text,
                status text not null default 'active',
                source_release_id text not null,
                created_by text not null,
                updated_by text not null,
                created_at timestamptz not null,
                updated_at timestamptz not null,
                primary key (bd_market, catalog_key, pid),
                constraint ck_promotion_catalog_market check (bd_market = 'mx'),
                constraint ck_promotion_catalog_key check (
                    catalog_key in ('viral','fashion','potential')
                ),
                constraint ck_promotion_catalog_pid check (
                    pid ~ '^[0-9]{10,32}$'
                ),
                constraint ck_promotion_catalog_seller check (
                    seller_id ~ '^[0-9]{10,32}$'
                ),
                constraint ck_promotion_catalog_status check (
                    status in ('active','archived')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_promotion_catalog_pid
                on promotion_catalog_product (bd_market, pid, status)
        """))
        connection.execute(text("""
            create table if not exists sample_confirmation_rule (
                rule_id text primary key,
                bd_market text not null,
                scope_type text not null,
                scope_value text not null,
                requires_confirmation boolean not null,
                status text not null default 'active',
                notes text,
                created_by text not null,
                updated_by text not null,
                created_at timestamptz not null,
                updated_at timestamptz not null,
                constraint uq_sample_confirmation_scope unique (
                    bd_market, scope_type, scope_value
                ),
                constraint ck_sample_confirmation_market check (
                    bd_market in ('mx','br','uk','it','jp','us','de')
                ),
                constraint ck_sample_confirmation_scope check (
                    scope_type in ('campaign','pid')
                ),
                constraint ck_sample_confirmation_status check (
                    status in ('active','inactive')
                )
            )
        """))
        connection.execute(text("""
            create table if not exists sample_sync_run (
                run_id text primary key,
                bd_market text not null,
                trigger_type text not null,
                status text not null,
                created_by text not null,
                started_at timestamptz not null,
                finished_at timestamptz,
                new_count bigint not null default 0,
                updated_count bigint not null default 0,
                error_count bigint not null default 0,
                error_code text,
                result_summary jsonb,
                constraint ck_sample_sync_market check (bd_market = 'mx'),
                constraint ck_sample_sync_trigger check (
                    trigger_type in ('scheduled','manual')
                ),
                constraint ck_sample_sync_status check (
                    status in ('running','succeeded','failed')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_sync_run_status
                on sample_sync_run (bd_market, status, started_at desc)
        """))
        connection.execute(text("""
            create table if not exists sample_request_current (
                request_id text primary key,
                bd_market text not null,
                creator_oec_id text,
                platform_creator_id text not null,
                creator_handle text,
                campaign_id text not null,
                campaign_name text,
                pid text not null,
                product_name text,
                shop_name text,
                seller_id text,
                applied_at timestamptz not null,
                review_deadline_at timestamptz,
                platform_status text not null,
                pool_type text not null default 'unclassified',
                gmv_mxn numeric,
                post_rate numeric,
                video_count bigint,
                average_views bigint,
                showcase_added boolean,
                sample_stock bigint,
                merchant_confirmation_status text not null default 'not_required',
                review_status text not null default 'pending',
                latest_sync_run_id text not null,
                source_payload jsonb not null default '{}'::jsonb,
                first_observed_at timestamptz not null,
                last_observed_at timestamptz not null,
                updated_at timestamptz not null,
                constraint fk_sample_request_sync foreign key (latest_sync_run_id)
                    references sample_sync_run(run_id),
                constraint ck_sample_request_market check (bd_market = 'mx'),
                constraint ck_sample_request_pid check (
                    pid ~ '^[0-9]{10,32}$'
                ),
                constraint ck_sample_request_platform_status check (
                    platform_status in (
                        'pending','approved','rejected','cancelled','expired'
                    )
                ),
                constraint ck_sample_request_pool check (
                    pool_type in ('viral','fashion','potential','unclassified')
                ),
                constraint ck_sample_request_confirmation check (
                    merchant_confirmation_status in (
                        'not_required','pending','sent','approved','rejected',
                        'no_response','overridden'
                    )
                ),
                constraint ck_sample_request_review check (
                    review_status in (
                        'pending','ready','approved','rejected','result_unknown','failed'
                    )
                ),
                constraint ck_sample_request_metrics check (
                    (gmv_mxn is null or gmv_mxn >= 0)
                    and (post_rate is null or post_rate >= 0)
                    and (video_count is null or video_count >= 0)
                    and (average_views is null or average_views >= 0)
                    and (sample_stock is null or sample_stock >= 0)
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_request_queue
                on sample_request_current (
                    bd_market, platform_status, review_status, review_deadline_at
                )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_request_campaign
                on sample_request_current (bd_market, campaign_id, applied_at)
        """))
        connection.execute(text("""
            create index if not exists ix_sample_request_pid
                on sample_request_current (bd_market, pid, applied_at)
        """))
        connection.execute(text("""
            create index if not exists ix_sample_request_creator_pid
                on sample_request_current (
                    bd_market, creator_oec_id, pid, applied_at desc
                )
        """))
        connection.execute(text("""
            create table if not exists sample_request_observation (
                observation_id text primary key,
                request_id text not null,
                sync_run_id text not null,
                observed_at timestamptz not null,
                platform_status text not null,
                payload_hash text not null,
                snapshot jsonb not null,
                constraint fk_sample_observation_request foreign key (request_id)
                    references sample_request_current(request_id),
                constraint fk_sample_observation_run foreign key (sync_run_id)
                    references sample_sync_run(run_id),
                constraint uq_sample_observation_payload unique (
                    request_id, payload_hash
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_observation_time
                on sample_request_observation (request_id, observed_at desc)
        """))
        connection.execute(text("""
            create table if not exists sample_weekly_creator_snapshot (
                bd_market text not null,
                week_start date not null,
                creator_oec_id text not null,
                creator_handle text,
                gmv_mxn numeric,
                tier_key text,
                mcn_bound boolean not null,
                captured_at timestamptz not null,
                source_updated_at timestamptz,
                primary key (bd_market, week_start, creator_oec_id),
                constraint ck_sample_weekly_market check (bd_market = 'mx'),
                constraint ck_sample_weekly_gmv check (
                    gmv_mxn is null or gmv_mxn >= 0
                )
            )
        """))
        connection.execute(text("""
            create table if not exists sample_quota_policy (
                policy_id text primary key,
                bd_market text not null,
                policy_version text not null,
                pool_type text not null,
                mcn_bound boolean not null,
                min_gmv_mxn numeric not null,
                max_gmv_mxn numeric,
                weekly_limit bigint not null,
                status text not null default 'active',
                effective_from date not null,
                effective_to date,
                created_at timestamptz not null default current_timestamp,
                constraint uq_sample_quota_policy unique (
                    bd_market, policy_version, pool_type, mcn_bound, min_gmv_mxn
                ),
                constraint ck_sample_quota_market check (bd_market = 'mx'),
                constraint ck_sample_quota_pool check (
                    pool_type in ('viral','potential')
                ),
                constraint ck_sample_quota_range check (
                    min_gmv_mxn >= 0
                    and (max_gmv_mxn is null or max_gmv_mxn > min_gmv_mxn)
                    and weekly_limit >= 0
                ),
                constraint ck_sample_quota_status check (
                    status in ('active','inactive')
                )
            )
        """))
        connection.execute(text("""
            create table if not exists sample_quota_event (
                event_id text primary key,
                request_id text not null,
                week_start date not null,
                pool_type text not null,
                event_type text not null,
                reserved_delta bigint not null,
                consumed_delta bigint not null,
                actor text not null,
                note text,
                occurred_at timestamptz not null,
                constraint uq_sample_quota_request_event unique (
                    request_id, event_type
                ),
                constraint fk_sample_quota_request foreign key (request_id)
                    references sample_request_current(request_id),
                constraint ck_sample_quota_event_pool check (
                    pool_type in ('viral','potential')
                ),
                constraint ck_sample_quota_event_type check (
                    event_type in ('reserve','consume','release','override')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_quota_event_week
                on sample_quota_event (week_start, pool_type, occurred_at)
        """))
        connection.execute(text("""
            create table if not exists sample_review_attempt (
                attempt_id text primary key,
                request_id text not null,
                action text not null,
                status text not null,
                actor text not null,
                reason text,
                request_dispatched_at timestamptz,
                finished_at timestamptz,
                http_status bigint,
                platform_code text,
                error_code text,
                response_metadata jsonb,
                created_at timestamptz not null,
                constraint fk_sample_review_request foreign key (request_id)
                    references sample_request_current(request_id),
                constraint ck_sample_review_action check (
                    action in ('approve','reject')
                ),
                constraint ck_sample_review_status check (
                    status in ('started','succeeded','result_unknown','failed')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_review_attempt_request
                on sample_review_attempt (request_id, created_at desc)
        """))

        policy_params = []
        for index, (pool, bound, minimum, maximum, limit) in enumerate(
            _POLICY_ROWS,
            start=1,
        ):
            policy_params.append({
                "policy_id": f"sqp_mx_v1_{index:02d}",
                "pool_type": pool,
                "mcn_bound": bound,
                "minimum": minimum,
                "maximum": maximum,
                "weekly_limit": limit,
                "effective_from": date(2026, 8, 24),
            })
        connection.execute(text("""
            insert into sample_quota_policy (
                policy_id, bd_market, policy_version, pool_type, mcn_bound,
                min_gmv_mxn, max_gmv_mxn, weekly_limit, status,
                effective_from, effective_to
            ) values (
                :policy_id, 'mx', 'mx-v1', :pool_type, :mcn_bound,
                :minimum, :maximum, :weekly_limit, 'active',
                :effective_from, null
            ) on conflict (policy_id) do nothing
        """), policy_params)


__all__ = ["apply_sample_management_v1"]
