"""修复联系方式 current 重复，并建立数据库级唯一约束。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_contact_current_integrity_v1(engine: Engine) -> None:
    """重建确定性的 current 投影，再建立部分唯一索引。

    迁移在一个事务中先锁住历史表，避免清理重复值与建索引之间又有旧 worker
    写入。SQLite 只用于轻量单元测试，不承载这组 PostgreSQL 生产表，保持安全
    no-op。
    """
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        # 与 Contact 正常事务保持相同的拿锁顺序：先历史 point，再兼容投影。
        # 维护窗口仍必须先让旧 worker 完全退出；一致顺序只是避免迟退事务死锁。
        connection.execute(text(
            "lock table creator_contact_point, creator_contact "
            "in share row exclusive mode"
        ))
        connection.execute(text("""
            update creator_contact
               set other_json = case
                       when other_json = 'null'::jsonb then null
                       else other_json
                   end,
                   raw_json = case
                       when raw_json = 'null'::jsonb then null
                       else raw_json
                   end
             where other_json = 'null'::jsonb
                or raw_json = 'null'::jsonb
        """))
        connection.execute(text("""
            update creator_contact_point
               set is_current = false
             where is_current is true
        """))
        connection.execute(text("""
            with ranked as (
                select
                    contact_point_id,
                    row_number() over (
                        partition by bd_market, oec_id, contact_type
                        order by
                            last_seen_at desc,
                            first_seen_at desc,
                            contact_point_id desc
                    ) as position
                from creator_contact_point
            )
            update creator_contact_point point
               set is_current = true
              from ranked
             where point.contact_point_id = ranked.contact_point_id
               and ranked.position = 1
        """))
        connection.execute(text("""
            create unique index if not exists
                uq_creator_contact_point_current
            on creator_contact_point (bd_market, oec_id, contact_type)
            where is_current is true
        """))
        # ``creator_contact`` 是无市场的兼容投影。若历史重复曾让它指向旧值，
        # 按全部市场中真实最新的 current 重新对齐；无历史的旧投影保持原值。
        connection.execute(text("""
            with ranked as (
                select
                    oec_id,
                    contact_type,
                    contact_value,
                    row_number() over (
                        partition by oec_id, contact_type
                        order by
                            last_seen_at desc,
                            first_seen_at desc,
                            contact_point_id desc
                    ) as position
                from creator_contact_point
                where is_current is true
                  and contact_type in ('whatsapp', 'email')
            ), latest as (
                select
                    oec_id,
                    max(contact_value) filter (
                        where contact_type = 'whatsapp'
                    ) as whatsapp,
                    max(contact_value) filter (
                        where contact_type = 'email'
                    ) as email
                from ranked
                where position = 1
                group by oec_id
            )
            update creator_contact contact
               set whatsapp = coalesce(latest.whatsapp, contact.whatsapp),
                   email = coalesce(latest.email, contact.email)
              from latest
             where contact.oec_id = latest.oec_id
        """))


__all__ = ["apply_contact_current_integrity_v1"]
