"""ShareLink 记录归属、执行人/达人和佣金修改版本指针。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_share_link_record_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            alter table share_link_job
                add column if not exists operator_name text,
                add column if not exists creator_name text,
                add column if not exists operation_type text not null default 'create'
        """))
        connection.execute(text("""
            update share_link_job
            set operation_type = 'catalog_refresh'
            where commission_config ->> 'catalog_refresh' = 'true'
        """))
        connection.execute(text("""
            do $$ begin
                if not exists (
                    select 1 from pg_constraint
                    where conname = 'ck_share_link_job_operation_type'
                ) then
                    alter table share_link_job add constraint
                        ck_share_link_job_operation_type check (
                            operation_type in ('create','commission_update','catalog_refresh')
                        );
                end if;
            end $$
        """))
        connection.execute(text("""
            create table if not exists share_link_record (
                record_id text primary key,
                bd_market text not null,
                pid text not null,
                operator_name text,
                creator_name text,
                current_plan_record_id text references share_link_plan(plan_record_id),
                latest_item_id text,
                status text not null,
                source text not null,
                created_at timestamptz not null,
                updated_at timestamptz not null,
                constraint ck_share_link_record_market check (
                    bd_market in ('mx','br','uk','it','jp','us','de')
                ),
                constraint ck_share_link_record_status check (
                    status in ('pending','active','result_unknown','failed','cancelled')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_share_link_record_operator
                on share_link_record (bd_market, operator_name, updated_at)
        """))
        connection.execute(text("""
            create index if not exists ix_share_link_record_creator
                on share_link_record (bd_market, creator_name, updated_at)
        """))
        connection.execute(text("""
            create index if not exists ix_share_link_record_pid
                on share_link_record (bd_market, pid, updated_at)
        """))
        connection.execute(text("""
            alter table share_link_item
                add column if not exists record_id text,
                add column if not exists operation_type text not null default 'create',
                add column if not exists target_campaign_id text
        """))
        connection.execute(text("""
            update share_link_item item
            set operation_type = job.operation_type
            from share_link_job job
            where job.job_id = item.job_id
        """))
        connection.execute(text("""
            do $$ begin
                if not exists (
                    select 1 from pg_constraint
                    where conname = 'fk_share_link_item_record'
                ) then
                    alter table share_link_item add constraint
                        fk_share_link_item_record foreign key (record_id)
                        references share_link_record(record_id);
                end if;
                if not exists (
                    select 1 from pg_constraint
                    where conname = 'ck_share_link_item_operation_type'
                ) then
                    alter table share_link_item add constraint
                        ck_share_link_item_operation_type check (
                            operation_type in ('create','commission_update','catalog_refresh')
                        );
                end if;
            end $$
        """))
        connection.execute(text("""
            insert into share_link_record (
                record_id, bd_market, pid, operator_name, creator_name,
                current_plan_record_id, latest_item_id, status, source,
                created_at, updated_at
            )
            select
                'slr_' || md5(plan.plan_record_id),
                plan.bd_market,
                plan.pid,
                null,
                null,
                plan.plan_record_id,
                (
                    select item.item_id
                    from share_link_item item
                    where item.plan_record_id = plan.plan_record_id
                    order by item.updated_at desc, item.item_id desc
                    limit 1
                ),
                'active',
                plan.source,
                plan.created_at,
                coalesce(plan.last_verified_at, plan.created_at)
            from share_link_plan plan
            on conflict (record_id) do nothing
        """))
        connection.execute(text("""
            update share_link_item item
            set record_id = 'slr_' || md5(item.plan_record_id)
            where item.plan_record_id is not null
              and item.record_id is null
        """))


__all__ = ["apply_share_link_record_v1"]
