"""schema v59：样品额度直接编辑审计与精简工作台支撑。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_sample_workspace_simplification_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text(
            "alter table sample_quota_policy "
            "add column if not exists updated_by text"
        ))
        connection.execute(text(
            "alter table sample_quota_policy "
            "add column if not exists updated_at timestamptz"
        ))
        connection.execute(text("""
            update sample_quota_policy
               set updated_by = coalesce(updated_by, 'schema_v59'),
                   updated_at = coalesce(updated_at, created_at, current_timestamp)
             where updated_by is null or updated_at is null
        """))
        connection.execute(text(
            "alter table sample_quota_policy alter column updated_by set not null"
        ))
        connection.execute(text(
            "alter table sample_quota_policy alter column updated_at set not null"
        ))
        connection.execute(text("""
            create table if not exists sample_quota_policy_event (
                event_id text primary key,
                policy_id text not null,
                old_weekly_limit bigint not null,
                new_weekly_limit bigint not null,
                actor text not null,
                occurred_at timestamptz not null,
                constraint fk_sample_quota_policy_event_policy
                    foreign key (policy_id) references sample_quota_policy(policy_id),
                constraint ck_sample_quota_policy_event_limits check (
                    old_weekly_limit >= 0 and new_weekly_limit >= 0
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_sample_quota_policy_event_time
                on sample_quota_policy_event (policy_id, occurred_at)
        """))


__all__ = ["apply_sample_workspace_simplification_v1"]
