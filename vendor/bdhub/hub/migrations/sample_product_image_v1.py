"""为样品审批保存可公开展示的商品缩略图。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_sample_product_image_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            alter table sample_request_current
                add column if not exists image_url text
        """))


__all__ = ["apply_sample_product_image_v1"]
