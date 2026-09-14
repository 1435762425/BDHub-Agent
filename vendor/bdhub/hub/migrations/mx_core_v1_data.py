"""MX 核心数据回填：官方 L1 GMV 峰值与现有联系方式。"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def apply_mx_core_v1_data(engine: Engine) -> None:
    """幂等回填；不删除任何历史表或联系方式。"""
    inspector = inspect(engine)
    has_history = inspector.has_table("creator_profile_history")
    history_union = ""
    if has_history:
        history_union = """
            union all
            select alias.bd_market, alias.oec_id, history.gmv_value,
                   history.captured_at::timestamptz
            from creator_profile_history history
            join creator_handle_alias alias
              on alias.bd_market = history.bd_market
             and alias.handle_key = history.handle_key
            where history.gmv_value is not null
        """

    with engine.begin() as connection:
        connection.execute(text(f"""
            with official_gmv as (
                select bd_market, oec_id, gmv_value, captured_at
                from creator_profile_snapshot
                where gmv_value is not null
                  and quality_status = 'accepted'
                  and lower(route) not like '%fastmoss%'
                union all
                select bd_market, oec_id, gmv_value, captured_at
                from creator_profile_current
                where gmv_value is not null
                union all
                select bd_market, oec_id, gmv_value,
                       captured_at::timestamptz
                from creator_profile
                where gmv_value is not null
                  and oec_id is not null
                  and oec_id <> ''
                {history_union}
            ), ranked as (
                select distinct on (bd_market, oec_id)
                       bd_market, oec_id, gmv_value, captured_at
                from official_gmv
                order by bd_market, oec_id, gmv_value desc,
                         captured_at asc nulls last
            )
            update creator_profile_current current
            set peak_gmv_value = ranked.gmv_value,
                peak_gmv_at = ranked.captured_at
            from ranked
            where current.bd_market = ranked.bd_market
              and current.oec_id = ranked.oec_id
              and (
                  current.peak_gmv_value is null
                  or ranked.gmv_value > current.peak_gmv_value
              )
        """))

        connection.execute(text("""
            insert into creator_contact_point (
                contact_point_id, bd_market, oec_id, contact_type,
                contact_value, value_hash, first_seen_at, last_seen_at,
                is_current, source
            )
            select md5(identity.bd_market || '|' || contact.oec_id ||
                       '|whatsapp|' || md5(lower(btrim(contact.whatsapp)))),
                   identity.bd_market, contact.oec_id, 'whatsapp',
                   btrim(contact.whatsapp),
                   md5(lower(btrim(contact.whatsapp))),
                   coalesce(contact.captured_at, current_timestamp),
                   coalesce(contact.captured_at, current_timestamp),
                   true, 'legacy_creator_contact'
            from creator_contact contact
            join creator_identity identity on identity.oec_id = contact.oec_id
            where nullif(btrim(contact.whatsapp), '') is not null
            on conflict (bd_market, oec_id, contact_type, value_hash)
            do update set
                last_seen_at = greatest(
                    creator_contact_point.last_seen_at,
                    excluded.last_seen_at
                ),
                is_current = true
        """))
        connection.execute(text("""
            insert into creator_contact_point (
                contact_point_id, bd_market, oec_id, contact_type,
                contact_value, value_hash, first_seen_at, last_seen_at,
                is_current, source
            )
            select md5(identity.bd_market || '|' || contact.oec_id ||
                       '|email|' || md5(lower(btrim(contact.email)))),
                   identity.bd_market, contact.oec_id, 'email',
                   btrim(contact.email),
                   md5(lower(btrim(contact.email))),
                   coalesce(contact.captured_at, current_timestamp),
                   coalesce(contact.captured_at, current_timestamp),
                   true, 'legacy_creator_contact'
            from creator_contact contact
            join creator_identity identity on identity.oec_id = contact.oec_id
            where nullif(btrim(contact.email), '') is not null
            on conflict (bd_market, oec_id, contact_type, value_hash)
            do update set
                last_seen_at = greatest(
                    creator_contact_point.last_seen_at,
                    excluded.last_seen_at
                ),
                is_current = true
        """))


__all__ = ["apply_mx_core_v1_data"]
