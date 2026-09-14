"""schema v57：推品网站草稿商品类目覆盖。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_promotion_site_category_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text(
            "alter table promotion_site_draft_item "
            "add column if not exists category_key text"
        ))
        connection.execute(text(
            "alter table promotion_site_draft_item "
            "drop constraint if exists ck_promotion_site_draft_item_category_key"
        ))
        connection.execute(text(
            "alter table promotion_site_draft_item "
            "add constraint ck_promotion_site_draft_item_category_key check ("
            "category_key is null or (length(trim(category_key)) between 3 and 120))"
        ))


__all__ = ["apply_promotion_site_category_v1"]
