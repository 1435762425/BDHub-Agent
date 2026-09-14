"""样品适用达人、唯一审批路线和官方女装版本表。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_sample_workflow_scope_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists sample_creator_eligibility (
                bd_market text not null,
                creator_oec_id text not null,
                creator_handle text,
                status text not null default 'active',
                notes text,
                created_by text not null,
                updated_by text not null,
                created_at timestamptz not null,
                updated_at timestamptz not null,
                primary key (bd_market, creator_oec_id),
                constraint ck_sample_creator_eligibility_market check (bd_market = 'mx'),
                constraint ck_sample_creator_eligibility_oec check (
                    creator_oec_id ~ '^[0-9]{10,32}$'
                ),
                constraint ck_sample_creator_eligibility_status check (
                    status in ('active','inactive')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_creator_eligibility_status
                on sample_creator_eligibility (bd_market, status, updated_at)
        """))
        connection.execute(text("""
            create table if not exists sample_creator_eligibility_event (
                event_id text primary key,
                bd_market text not null,
                creator_oec_id text not null,
                creator_handle text,
                source_type text not null,
                event_type text not null,
                actor text not null,
                notes text,
                occurred_at timestamptz not null,
                constraint ck_sample_creator_event_market check (bd_market = 'mx'),
                constraint ck_sample_creator_event_source check (
                    source_type in ('manual_non_mcn','mcn_relation')
                ),
                constraint ck_sample_creator_event_type check (
                    event_type in ('activated','deactivated','source_observed')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_creator_event_identity
                on sample_creator_eligibility_event (
                    bd_market, creator_oec_id, occurred_at
                )
        """))
        connection.execute(text("""
            create table if not exists sample_official_product_form (
                form_version_id text primary key,
                bd_market text not null,
                version_label text not null,
                source_name text not null,
                source_sha256 text not null,
                status text not null,
                row_count bigint not null,
                created_by text not null,
                created_at timestamptz not null,
                activated_at timestamptz,
                constraint ck_sample_official_form_market check (bd_market = 'mx'),
                constraint ck_sample_official_form_status check (
                    status in ('draft','active','archived')
                ),
                constraint ck_sample_official_form_count check (row_count >= 0),
                constraint uq_sample_official_form_version_label unique (
                    bd_market, version_label
                )
            )
        """))
        connection.execute(text("""
            create unique index if not exists uq_sample_official_form_active
                on sample_official_product_form (bd_market)
                where status = 'active'
        """))
        connection.execute(text("""
            create table if not exists sample_official_product_form_item (
                form_version_id text not null,
                pid text not null,
                seller_id text,
                product_name text,
                image_url text,
                created_at timestamptz not null,
                primary key (form_version_id, pid),
                constraint fk_sample_official_form_item_version
                    foreign key (form_version_id)
                    references sample_official_product_form(form_version_id),
                constraint ck_sample_official_form_item_pid check (
                    pid ~ '^[0-9]{10,32}$'
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_official_form_item_pid
                on sample_official_product_form_item (pid, form_version_id)
        """))
        connection.execute(text("""
            create table if not exists sample_product_approval_route (
                bd_market text not null,
                pid text not null,
                approval_route text not null,
                source_kind text not null,
                source_form_version_id text,
                notes text,
                created_by text not null,
                updated_by text not null,
                created_at timestamptz not null,
                updated_at timestamptz not null,
                primary key (bd_market, pid),
                constraint fk_sample_product_route_form
                    foreign key (source_form_version_id)
                    references sample_official_product_form(form_version_id),
                constraint ck_sample_product_route_market check (bd_market = 'mx'),
                constraint ck_sample_product_route_pid check (
                    pid ~ '^[0-9]{10,32}$'
                ),
                constraint ck_sample_product_route_value check (
                    approval_route in (
                        'official_managed','agency_managed','not_applicable'
                    )
                ),
                constraint ck_sample_product_route_source check (
                    source_kind in ('manual','official_form','catalog')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_product_route_value
                on sample_product_approval_route (
                    bd_market, approval_route, updated_at
                )
        """))
        connection.execute(text("""
            insert into sample_product_approval_route (
                bd_market, pid, approval_route, source_kind,
                source_form_version_id, notes, created_by, updated_by,
                created_at, updated_at
            )
            select
                bd_market, pid, 'agency_managed', 'catalog',
                null, 'seeded_from_active_catalog', 'schema_v43', 'schema_v43',
                current_timestamp, current_timestamp
            from promotion_catalog_product
            where bd_market = 'mx'
              and catalog_key in ('viral','potential')
              and status = 'active'
            group by bd_market, pid
            on conflict (bd_market, pid) do nothing
        """))


__all__ = ["apply_sample_workflow_scope_v1"]
