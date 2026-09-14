# -*- coding: utf-8 -*-
"""PostgreSQL 大批量文本集合查询工具。"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Text, any_, bindparam
from sqlalchemy.dialects.postgresql import ARRAY


def text_array_member(column, values: Iterable[str], *, parameter: str):
    """用一个 ``text[]`` 参数表达集合成员关系，避免展开海量 bind 参数。"""
    items = list(values)
    if not items:
        raise ValueError("text_array_member_values_required")
    return column == any_(bindparam(
        parameter,
        value=items,
        type_=ARRAY(Text()),
    ))


__all__ = ["text_array_member"]
