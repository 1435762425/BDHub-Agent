"""商家管理、导入预览和操作审计。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_merchant_management_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists merchant (
                bd_market text not null,
                seller_id text not null,
                shop_name text not null,
                relationship_status text not null default 'unknown',
                relationship_source text not null default 'manual',
                primary_category text,
                merchant_grade text,
                rating numeric,
                sales bigint,
                owner text,
                email text,
                whatsapp text,
                phone text,
                country_code text,
                notes text,
                source_file text,
                created_by text not null,
                updated_by text not null,
                created_at timestamptz not null,
                updated_at timestamptz not null,
                primary key (bd_market, seller_id),
                constraint ck_merchant_market check (
                    bd_market in ('mx','br','uk','it','jp','us','de')
                ),
                constraint ck_merchant_seller_id check (
                    seller_id ~ '^[0-9]{10,32}$'
                ),
                constraint ck_merchant_relationship_status check (
                    relationship_status in (
                        'connected','not_connected','unknown'
                    )
                ),
                constraint ck_merchant_grade check (
                    merchant_grade is null
                    or merchant_grade in ('S','A','B','C')
                ),
                constraint ck_merchant_rating check (
                    rating is null or (rating >= 0 and rating <= 5)
                ),
                constraint ck_merchant_sales check (
                    sales is null or sales >= 0
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_merchant_relationship
                on merchant (bd_market, relationship_status, updated_at)
        """))
        connection.execute(text("""
            create index if not exists ix_merchant_owner
                on merchant (bd_market, owner, updated_at)
        """))
        connection.execute(text("""
            create index if not exists ix_merchant_category
                on merchant (bd_market, primary_category, merchant_grade)
        """))
        connection.execute(text("""
            create table if not exists merchant_event (
                event_id text primary key,
                bd_market text not null,
                seller_id text not null,
                event_type text not null,
                before_payload jsonb,
                after_payload jsonb,
                source text not null,
                import_id text,
                actor text not null,
                occurred_at timestamptz not null,
                constraint ck_merchant_event_type check (
                    event_type in ('create','update','import_insert','import_update')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_merchant_event_timeline
                on merchant_event (bd_market, seller_id, occurred_at)
        """))
        connection.execute(text("""
            create table if not exists merchant_import_batch (
                import_id text primary key,
                bd_market text not null,
                status text not null,
                file_name text not null,
                sha256 text not null,
                row_count bigint not null,
                insert_count bigint not null default 0,
                update_count bigint not null default 0,
                unchanged_count bigint not null default 0,
                protected_count bigint not null default 0,
                error_count bigint not null default 0,
                created_by text not null,
                created_at timestamptz not null,
                applied_at timestamptz,
                result_summary jsonb,
                constraint ck_merchant_import_batch_market check (
                    bd_market in ('mx','br','uk','it','jp','us','de')
                ),
                constraint ck_merchant_import_batch_status check (
                    status in ('previewed','applied','failed','expired')
                )
            )
        """))
        connection.execute(text("""
            create table if not exists merchant_import_row (
                import_id text not null,
                row_number bigint not null,
                status text not null,
                seller_id text,
                payload jsonb,
                error_code text,
                primary key (import_id, row_number),
                constraint fk_merchant_import_row_batch foreign key (import_id)
                    references merchant_import_batch(import_id) on delete cascade,
                constraint ck_merchant_import_row_status check (
                    status in (
                        'insert','update','unchanged','protected_connected',
                        'duplicate_same','duplicate_conflict','invalid'
                    )
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_merchant_import_row_status
                on merchant_import_row (import_id, status, row_number)
        """))


__all__ = ["apply_merchant_management_v1"]
