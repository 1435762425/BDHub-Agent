"""墨西哥西语公开升佣请求的最小持久状态。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_public_share_link_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            create table if not exists public_share_link_request (
                request_id text primary key,
                token_hash text not null unique,
                bd_market text not null,
                pid text not null,
                status text not null,
                job_id text references share_link_job(job_id),
                record_id text references share_link_record(record_id),
                ip_hash text not null,
                claim_token text,
                claimed_at timestamptz,
                error_code text,
                created_at timestamptz not null,
                updated_at timestamptz not null,
                expires_at timestamptz not null,
                constraint ck_public_share_link_request_market check (
                    bd_market = 'mx'
                ),
                constraint ck_public_share_link_request_status check (
                    status in ('queued','verifying','generating','succeeded',
                        'ineligible','result_unknown','failed')
                )
            )
        """))
        connection.execute(text("""
            create index if not exists ix_public_share_link_request_status
                on public_share_link_request (status, created_at)
        """))
        connection.execute(text("""
            create index if not exists ix_public_share_link_request_ip
                on public_share_link_request (ip_hash, created_at)
        """))
        connection.execute(text("""
            create index if not exists ix_public_share_link_request_pid
                on public_share_link_request (pid, created_at)
        """))


__all__ = ["apply_public_share_link_v1"]
