"""schema v58：允许Campaign商品保存可继承的Codex AI类目。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_campaign_ai_category_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            alter table product_source_pool_item
                drop constraint if exists ck_product_source_pool_category_method
        """))
        connection.execute(text("""
            alter table product_source_pool_item
                add constraint ck_product_source_pool_category_method check (
                    category_mapping_method in (
                        'source_full_managed','source_alias','title_rule',
                        'unclassified','ai_classified'
                    )
                )
        """))


__all__ = ["apply_campaign_ai_category_v1"]
