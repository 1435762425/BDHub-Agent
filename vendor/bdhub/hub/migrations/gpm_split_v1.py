"""Profile [2] 直播/视频 GPM 字段迁移。

官方响应把两个字段放在 ``ec_live_gpm`` / ``ec_video_gpm`` 下，值包装为
``value.minimal`` 与 ``value.maximum``。当前接口样本两端相同，因此可以安全
投影为一个数值；若未来变成真正区间，迁移和解析都会保留为空，不伪造单点值。
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


_TABLES = (
    "creator_profile",
    "creator_profile_snapshot",
    "creator_profile_current",
)


def _value_expression(raw_column: str, field_name: str) -> str:
    """返回从 JSONB 中提取精确 GPM 的 SQL CASE 表达式。"""
    raw = f"{raw_column}->'{field_name}'->'value'"
    minimum = f"{raw}->>'minimal'"
    maximum = f"{raw}->>'maximum'"
    return f"""
        case
            when jsonb_typeof({raw}) = 'object'
             and nullif({minimum}, '') is not null
             and nullif({maximum}, '') is not null
             and {minimum} = {maximum}
                then nullif({minimum}, '')::numeric
            when jsonb_typeof({raw}) = 'number'
                then ({raw})::text::numeric
            else null
        end
    """


def apply_gpm_split_v1(engine: Engine) -> None:
    """新增 typed GPM 列，并从历史快照回填可确认的精确值。"""
    if engine.dialect.name != "postgresql":
        return

    live_expr = _value_expression("raw_json", "ec_live_gpm")
    video_expr = _value_expression("raw_json", "ec_video_gpm")
    with engine.begin() as connection:
        for table in _TABLES:
            connection.execute(text(
                f"alter table {table} add column if not exists live_gpm numeric"
            ))
            connection.execute(text(
                f"alter table {table} add column if not exists video_gpm numeric"
            ))

        # 快照是唯一保留完整原始响应的历史表。先把它补成可查询的 typed 值。
        connection.execute(text(f"""
            update creator_profile_snapshot
               set live_gpm = {live_expr},
                   video_gpm = {video_expr}
             where raw_json is not null
               and (raw_json ? 'ec_live_gpm' or raw_json ? 'ec_video_gpm')
        """))
        connection.execute(text("""
            update creator_profile_snapshot
               set parsed_json = parsed_json || jsonb_strip_nulls(
                   jsonb_build_object('live_gpm', live_gpm, 'video_gpm', video_gpm)
               )
             where live_gpm is not null or video_gpm is not null
        """))

        # 每个字段按各自最近一次有效观测回填，避免新快照缺字段覆盖旧值。
        connection.execute(text("""
            with live as (
                select distinct on (bd_market, oec_id)
                    bd_market, oec_id, live_gpm, snapshot_id, captured_at
                  from creator_profile_snapshot
                 where live_gpm is not null
                 order by bd_market, oec_id, captured_at desc, snapshot_id desc
            ), video as (
                select distinct on (bd_market, oec_id)
                    bd_market, oec_id, video_gpm, snapshot_id, captured_at
                  from creator_profile_snapshot
                 where video_gpm is not null
                 order by bd_market, oec_id, captured_at desc, snapshot_id desc
            ), merged as (
                select
                    coalesce(live.bd_market, video.bd_market) as bd_market,
                    coalesce(live.oec_id, video.oec_id) as oec_id,
                    live.live_gpm,
                    live.snapshot_id as live_snapshot_id,
                    live.captured_at as live_captured_at,
                    video.video_gpm,
                    video.snapshot_id as video_snapshot_id,
                    video.captured_at as video_captured_at
                  from live
                  full outer join video
                    on video.bd_market = live.bd_market
                   and video.oec_id = live.oec_id
            )
            update creator_profile_current current
               set live_gpm = coalesce(merged.live_gpm, current.live_gpm),
                   video_gpm = coalesce(merged.video_gpm, current.video_gpm),
                   field_sources =
                       coalesce(current.field_sources, '{}'::jsonb)
                       || case when merged.live_snapshot_id is not null then
                           jsonb_build_object('live_gpm', jsonb_build_object(
                               'snapshot_id', merged.live_snapshot_id,
                               'observed_at', merged.live_captured_at
                           )) else '{}'::jsonb end
                       || case when merged.video_snapshot_id is not null then
                           jsonb_build_object('video_gpm', jsonb_build_object(
                               'snapshot_id', merged.video_snapshot_id,
                               'observed_at', merged.video_captured_at
                           )) else '{}'::jsonb end
              from merged
             where current.bd_market = merged.bd_market
               and current.oec_id = merged.oec_id
        """))

        # 兼容旧 creator_profile 镜像，避免仍有旧读路径时出现字段断层。
        connection.execute(text("""
            update creator_profile legacy
               set live_gpm = current.live_gpm,
                   video_gpm = current.video_gpm
              from creator_profile_current current
             where legacy.bd_market = current.bd_market
               and legacy.oec_id = current.oec_id
        """))


__all__ = ["apply_gpm_split_v1"]
