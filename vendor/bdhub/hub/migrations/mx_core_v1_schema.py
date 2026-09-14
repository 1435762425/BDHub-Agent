"""MX 核心数据模型扩展：GMV 峰值与联系方式永久历史。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_mx_core_v1_schema(engine: Engine) -> None:
    """只执行可重复的 DDL；历史数据回填由下一版本单独完成。"""
    with engine.begin() as connection:
        connection.execute(text(
            "alter table creator_profile_current "
            "add column if not exists peak_gmv_value numeric"
        ))
        connection.execute(text(
            "alter table creator_profile_current "
            "add column if not exists peak_gmv_at timestamptz"
        ))
        connection.execute(text("""
            create table if not exists creator_contact_point (
                contact_point_id text primary key,
                bd_market text not null,
                oec_id text not null,
                contact_type text not null,
                contact_value text not null,
                value_hash text not null,
                first_seen_at timestamptz not null,
                last_seen_at timestamptz not null,
                is_current boolean not null default true,
                source text not null,
                constraint uq_creator_contact_point_value unique (
                    bd_market, oec_id, contact_type, value_hash
                )
            )
        """))
        connection.execute(text(
            "create index if not exists ix_creator_contact_point_identity "
            "on creator_contact_point "
            "(bd_market, oec_id, contact_type)"
        ))


__all__ = ["apply_mx_core_v1_schema"]
