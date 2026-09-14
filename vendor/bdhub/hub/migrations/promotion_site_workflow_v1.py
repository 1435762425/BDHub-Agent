from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_promotion_site_workflow_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    statements = (
        """
        create table if not exists promotion_site_page (
            page_id text primary key,
            bd_market text not null,
            page_name text not null,
            slug text not null,
            page_type text not null,
            status text not null default 'active',
            sort_order bigint not null default 0,
            created_by text not null,
            updated_by text not null,
            created_at timestamptz not null,
            updated_at timestamptz not null,
            constraint ck_promotion_site_page_market check (bd_market = 'mx'),
            constraint ck_promotion_site_page_type check (page_type in ('full_managed','campaign')),
            constraint ck_promotion_site_page_status check (status in ('active','inactive')),
            constraint ck_promotion_site_page_slug check (slug ~ '^[a-z0-9][a-z0-9-]{1,79}$'),
            constraint uq_promotion_site_page_slug unique (bd_market, slug)
        )
        """,
        """
        create unique index if not exists uq_promotion_site_page_full_managed
            on promotion_site_page (bd_market, page_type)
            where page_type = 'full_managed'
        """,
        """
        create index if not exists ix_promotion_site_page_order
            on promotion_site_page (bd_market, status, sort_order, page_id)
        """,
        """
        create table if not exists promotion_site_release (
            release_id text primary key,
            bd_market text not null,
            version_label text not null,
            status text not null,
            payload jsonb not null,
            source_sha256 text not null,
            page_count bigint not null,
            product_count bigint not null,
            validation_summary jsonb not null,
            remote_release_id text,
            created_by text not null,
            created_at timestamptz not null,
            staged_at timestamptz,
            activated_at timestamptz,
            constraint ck_promotion_site_release_market check (bd_market = 'mx'),
            constraint ck_promotion_site_release_status check (status in ('staged','production','archived','failed')),
            constraint ck_promotion_site_release_counts check (page_count >= 0 and product_count >= 0),
            constraint uq_promotion_site_release_label unique (bd_market, version_label)
        )
        """,
        """
        create unique index if not exists uq_promotion_site_release_production
            on promotion_site_release (bd_market)
            where status = 'production'
        """,
        """
        create index if not exists ix_promotion_site_release_status
            on promotion_site_release (bd_market, status, created_at desc)
        """,
        """
        create table if not exists promotion_site_draft (
            draft_id text primary key,
            bd_market text not null,
            base_release_id text,
            status text not null default 'open',
            revision bigint not null default 1,
            created_by text not null,
            updated_by text not null,
            created_at timestamptz not null,
            updated_at timestamptz not null,
            constraint fk_promotion_site_draft_release foreign key (base_release_id) references promotion_site_release(release_id),
            constraint ck_promotion_site_draft_market check (bd_market = 'mx'),
            constraint ck_promotion_site_draft_status check (status in ('open','released','discarded')),
            constraint ck_promotion_site_draft_revision check (revision >= 1)
        )
        """,
        """
        create unique index if not exists uq_promotion_site_draft_open
            on promotion_site_draft (bd_market)
            where status = 'open'
        """,
        """
        create table if not exists promotion_site_draft_item (
            draft_id text not null,
            page_id text not null,
            pid text not null,
            source_pool_key text not null,
            sort_order bigint not null,
            added_by text not null,
            added_at timestamptz not null,
            primary key (draft_id, page_id, pid),
            constraint fk_promotion_site_draft_item_draft foreign key (draft_id) references promotion_site_draft(draft_id) on delete cascade,
            constraint fk_promotion_site_draft_item_page foreign key (page_id) references promotion_site_page(page_id),
            constraint ck_promotion_site_draft_item_pid check (pid ~ '^[0-9]{10,32}$'),
            constraint ck_promotion_site_draft_item_source check (source_pool_key in ('full_managed','campaign')),
            constraint ck_promotion_site_draft_item_order check (sort_order >= 0)
        )
        """,
        """
        create index if not exists ix_promotion_site_draft_item_pid
            on promotion_site_draft_item (draft_id, pid, page_id)
        """,
        """
        create index if not exists ix_promotion_site_draft_item_page_order
            on promotion_site_draft_item (draft_id, page_id, sort_order, pid)
        """,
        """
        insert into promotion_site_page (
            page_id, bd_market, page_name, slug, page_type, status, sort_order,
            created_by, updated_by, created_at, updated_at
        ) values (
            'psp_mx_full_managed', 'mx', 'Productos Oficiales de TikTok', 'tiktok-oficial',
            'full_managed', 'active', 10, 'migration_v53', 'migration_v53', now(), now()
        ) on conflict (page_id) do nothing
        """,
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


__all__ = ["apply_promotion_site_workflow_v1"]
