"""物理移除已取消且为空的可用商品池表。"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def apply_retire_available_product_pool(engine: Engine) -> None:
    """仅在两张旧表均为空时删除；发现数据则 fail-closed。"""
    inspector = inspect(engine)
    table_names = (
        "available_product_current",
        "available_product_import",
    )
    with engine.connect() as connection:
        for table_name in table_names:
            if not inspector.has_table(table_name):
                continue
            count = connection.execute(
                text(f"select count(*) from {table_name}")
            ).scalar_one()
            if count:
                raise RuntimeError(
                    f"retired_available_product_table_not_empty:{table_name}:{count}"
                )

    with engine.begin() as connection:
        connection.execute(text(
            "drop table if exists available_product_current"
        ))
        connection.execute(text(
            "drop table if exists available_product_import"
        ))


__all__ = ["apply_retire_available_product_pool"]
