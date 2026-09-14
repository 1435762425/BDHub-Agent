"""爆款和成长机会货盘的版本化导入底座。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_product_catalog_version_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            alter table sample_workflow_import_batch
                drop constraint if exists ck_sample_workflow_import_type
        """))
        connection.execute(text("""
            alter table sample_workflow_import_batch
                add constraint ck_sample_workflow_import_type check (
                    import_type in (
                        'creator_eligibility','official_product_form','product_catalog'
                    )
                )
        """))
        connection.execute(text("""
            create table if not exists product_catalog_version (
                version_id text primary key,
                bd_market text not null,
                catalog_key text not null,
                version_label text not null,
                source_name text not null,
                source_sha256 text not null,
                status text not null,
                row_count bigint not null,
                created_by text not null,
                created_at timestamptz not null,
                activated_at timestamptz,
                constraint ck_product_catalog_version_market check (bd_market = 'mx'),
                constraint ck_product_catalog_version_key check (
                    catalog_key in ('viral','opportunity')
                ),
                constraint ck_product_catalog_version_status check (
                    status in ('active','archived')
                ),
                constraint uq_product_catalog_version_label unique (
                    bd_market, catalog_key, version_label
                )
            )
        """))
        connection.execute(text("""
            create unique index if not exists uq_product_catalog_version_active
                on product_catalog_version (bd_market, catalog_key)
                where status = 'active'
        """))
        connection.execute(text("""
            create table if not exists product_catalog_item (
                version_id text not null,
                pid text not null,
                seller_id text not null,
                product_name text,
                image_url text,
                category text,
                created_at timestamptz not null,
                primary key (version_id, pid),
                constraint fk_product_catalog_item_version foreign key (version_id)
                    references product_catalog_version(version_id),
                constraint ck_product_catalog_item_pid check (
                    pid ~ '^[0-9]{10,32}$'
                ),
                constraint ck_product_catalog_item_seller check (
                    seller_id ~ '^[0-9]{10,32}$'
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_product_catalog_item_pid
                on product_catalog_item (pid, version_id)
        """))


__all__ = ["apply_product_catalog_version_v1"]
