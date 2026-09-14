"""商品规范一级类目、映射方法与规则版本。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..product_categories import classify_product_category


def apply_product_category_normalization_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        for statement in (
            "alter table product_source_pool_item add column if not exists canonical_category_l1 text not null default '待归类'",
            "alter table product_source_pool_item add column if not exists category_mapping_method text not null default 'unclassified'",
            "alter table product_source_pool_item add column if not exists category_rule_version text not null default 'mx-canonical-v1'",
        ):
            connection.execute(text(statement))
        rows = connection.execute(text("""
            select item.version_id,
                   item.pid,
                   version.pool_key,
                   item.category_l1,
                   item.category_l2,
                   item.category_l3,
                   item.product_name,
                   item.source_attributes
              from product_source_pool_item item
              join product_source_pool_version version
                on version.version_id = item.version_id
        """)).mappings().all()
        updates = []
        for row in rows:
            decision = classify_product_category(
                pool_key=str(row["pool_key"]),
                category_l1=row["category_l1"],
                category_l2=row["category_l2"],
                category_l3=row["category_l3"],
                product_name=row["product_name"],
                source_attributes=row["source_attributes"] or {},
            )
            updates.append({
                "version_id": row["version_id"],
                "pid": row["pid"],
                "canonical_category_l1": decision.category,
                "category_mapping_method": decision.method,
                "category_rule_version": decision.rule_version,
            })
        if updates:
            connection.execute(text("""
                update product_source_pool_item
                   set canonical_category_l1 = :canonical_category_l1,
                       category_mapping_method = :category_mapping_method,
                       category_rule_version = :category_rule_version
                 where version_id = :version_id
                   and pid = :pid
            """), updates)
        connection.execute(text("""
            alter table product_source_pool_item
                drop constraint if exists ck_product_source_pool_category_method
        """))
        connection.execute(text("""
            alter table product_source_pool_item
                add constraint ck_product_source_pool_category_method check (
                    category_mapping_method in (
                        'source_full_managed','source_alias','title_rule','unclassified'
                    )
                )
        """))
        connection.execute(text("""
            alter table product_source_pool_item
                drop constraint if exists ck_product_source_pool_canonical_category
        """))
        connection.execute(text("""
            alter table product_source_pool_item
                add constraint ck_product_source_pool_canonical_category check (
                    length(trim(canonical_category_l1)) > 0
                    and length(trim(category_rule_version)) > 0
                )
        """))
        connection.execute(text("""
            create index if not exists ix_product_source_pool_item_canonical_category
                on product_source_pool_item (canonical_category_l1, version_id)
        """))
        connection.execute(text("""
            update sample_product_approval_route route
               set approval_route = 'not_applicable',
                   notes = case
                       when coalesce(route.notes, '') like '%full_managed_no_bjn_sample%'
                           then route.notes
                       else coalesce(route.notes, '') ||
                           case when coalesce(route.notes, '') = '' then '' else ';' end ||
                           'full_managed_no_bjn_sample'
                   end,
                   updated_by = 'schema_v51',
                   updated_at = current_timestamp
             where route.bd_market = 'mx'
               and exists (
                    select 1
                      from product_source_pool_item item
                      join product_source_pool_version version
                        on version.version_id = item.version_id
                     where version.bd_market = 'mx'
                       and version.pool_key = 'full_managed'
                       and version.status = 'active'
                       and item.pid = route.pid
               )
               and (
                    route.approval_route <> 'not_applicable'
                    or coalesce(route.notes, '') not like '%full_managed_no_bjn_sample%'
               )
        """))


__all__ = ["apply_product_category_normalization_v1"]
