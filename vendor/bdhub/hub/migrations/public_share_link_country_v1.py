"""公开升佣请求增加Cloudflare两位来源国家代码。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_public_share_link_country_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("""
            alter table public_share_link_request
                add column if not exists country_code text
        """))
        connection.execute(text("""
            do $$
            begin
                if not exists (
                    select 1 from pg_constraint
                    where conname = 'ck_public_share_link_request_country'
                ) then
                    alter table public_share_link_request
                    add constraint ck_public_share_link_request_country
                    check (
                        country_code is null
                        or country_code ~ '^[A-Z0-9]{2}$'
                    );
                end if;
            end
            $$
        """))
        connection.execute(text("""
            create index if not exists ix_public_share_link_request_country
                on public_share_link_request (country_code, created_at)
        """))


__all__ = ["apply_public_share_link_country_v1"]
