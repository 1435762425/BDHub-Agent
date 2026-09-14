"""修正联系方式平台提示的三态语义。

部分 Profile 响应把 ``contact_info_available`` 返回为授权元数据对象，旧解析
曾把它投影成 ``False``。迁移只修正 current/legacy 投影，历史快照保持不可变。
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_contact_hint_semantics_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        connection.execute(text("""
            with affected as (
                select
                    current.bd_market,
                    current.oec_id
                from creator_profile_current current
                join creator_profile_snapshot source
                  on source.snapshot_id =
                     current.field_sources->'contact_available'->>'snapshot_id'
                where jsonb_typeof(
                          source.raw_json->'contact_info_available'
                      ) = 'object'
                  and not (
                      source.raw_json->'contact_info_available' ? 'value'
                  )
            ), explicit as (
                select distinct on (snapshot.bd_market, snapshot.oec_id)
                    snapshot.bd_market,
                    snapshot.oec_id,
                    snapshot.snapshot_id,
                    snapshot.captured_at,
                    case
                        when jsonb_typeof(
                                 snapshot.raw_json->'contact_info_available'
                             ) = 'boolean'
                            then (
                                snapshot.raw_json->>'contact_info_available'
                            )::boolean
                        when jsonb_typeof(
                                 snapshot.raw_json
                                 ->'contact_info_available'->'value'
                             ) = 'boolean'
                            then (
                                snapshot.raw_json
                                ->'contact_info_available'->>'value'
                            )::boolean
                    end as hint
                from creator_profile_snapshot snapshot
                join affected
                  on affected.bd_market = snapshot.bd_market
                 and affected.oec_id = snapshot.oec_id
                where jsonb_typeof(
                          snapshot.raw_json->'contact_info_available'
                      ) = 'boolean'
                   or jsonb_typeof(
                          snapshot.raw_json
                          ->'contact_info_available'->'value'
                      ) = 'boolean'
                order by
                    snapshot.bd_market,
                    snapshot.oec_id,
                    snapshot.captured_at desc,
                    snapshot.snapshot_id desc
            ), resolved as (
                select
                    affected.bd_market,
                    affected.oec_id,
                    explicit.snapshot_id,
                    explicit.captured_at,
                    explicit.hint
                from affected
                left join explicit
                  on explicit.bd_market = affected.bd_market
                 and explicit.oec_id = affected.oec_id
            )
            update creator_profile_current current
               set contact_available = resolved.hint,
                   field_sources =
                       case
                           when resolved.snapshot_id is null
                               then current.field_sources - 'contact_available'
                           else jsonb_set(
                               current.field_sources,
                               '{contact_available}',
                               jsonb_build_object(
                                   'snapshot_id', resolved.snapshot_id,
                                   'observed_at', resolved.captured_at
                               ),
                               true
                           )
                       end
              from resolved
             where current.bd_market = resolved.bd_market
               and current.oec_id = resolved.oec_id
        """))
        connection.execute(text("""
            update creator_profile legacy
               set contact_available = current.contact_available
              from creator_profile_current current
             where legacy.bd_market = current.bd_market
               and legacy.oec_id = current.oec_id
        """))


__all__ = ["apply_contact_hint_semantics_v1"]
