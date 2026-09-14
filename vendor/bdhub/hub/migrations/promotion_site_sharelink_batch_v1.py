from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_promotion_site_sharelink_batch_v1(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    statements = (
        """
        create table if not exists promotion_site_sharelink_batch (
            batch_id text primary key,
            draft_id text not null,
            status text not null,
            account_name text not null,
            revision bigint not null default 1,
            created_by text not null,
            created_at timestamptz not null,
            updated_at timestamptz not null,
            confirmed_at timestamptz,
            finished_at timestamptz,
            constraint fk_promotion_site_sharelink_batch_draft foreign key (draft_id) references promotion_site_draft(draft_id),
            constraint ck_promotion_site_sharelink_batch_status check (status in ('previewing','ready','running','completed','completed_with_errors','needs_review','failed','cancelled')),
            constraint ck_promotion_site_sharelink_batch_revision check (revision >= 1)
        )
        """,
        """
        create unique index if not exists uq_promotion_site_sharelink_batch_active
            on promotion_site_sharelink_batch (draft_id)
            where status in ('previewing','ready','running')
        """,
        """
        create index if not exists ix_promotion_site_sharelink_batch_status
            on promotion_site_sharelink_batch (status, updated_at, batch_id)
        """,
        """
        create table if not exists promotion_site_sharelink_item (
            batch_id text not null,
            pid text not null,
            sort_order bigint not null,
            status text not null,
            job_id text,
            record_id text,
            error_code text,
            created_at timestamptz not null,
            updated_at timestamptz not null,
            primary key (batch_id, pid),
            constraint fk_promotion_site_sharelink_item_batch foreign key (batch_id) references promotion_site_sharelink_batch(batch_id) on delete cascade,
            constraint fk_promotion_site_sharelink_item_job foreign key (job_id) references share_link_job(job_id),
            constraint fk_promotion_site_sharelink_item_record foreign key (record_id) references share_link_record(record_id),
            constraint ck_promotion_site_sharelink_item_pid check (pid ~ '^[0-9]{10,32}$'),
            constraint ck_promotion_site_sharelink_item_order check (sort_order >= 1),
            constraint ck_promotion_site_sharelink_item_status check (status in ('pending_preview','previewing','ready','generating','succeeded','ineligible','failed','result_unknown','cancelled'))
        )
        """,
        """
        create index if not exists ix_promotion_site_sharelink_item_status
            on promotion_site_sharelink_item (batch_id, status, sort_order, pid)
        """,
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


__all__ = ["apply_promotion_site_sharelink_batch_v1"]
