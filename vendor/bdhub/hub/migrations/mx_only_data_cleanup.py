"""Remove retired-market data and strictly orphaned outreach rows."""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


RETIRED_MARKETS = ("jp", "uk", "us")
MARKET_TABLES = (
    ("im_message", "bd_market"),
    ("im_conversation", "bd_market"),
    ("creator_profile_snapshot", "bd_market"),
    ("creator_handle_alias", "bd_market"),
    ("creator_profile_current", "bd_market"),
    ("creator_identity", "bd_market"),
    ("creator_profile", "bd_market"),
    ("miss_log", "bd_market"),
)


def apply_mx_only_data_cleanup(engine: Engine) -> None:
    """Delete only retired JP/UK/US rows; preserve every contact record."""
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    with engine.begin() as connection:
        contact_count_before = 0
        if "creator_contact_point" in existing:
            contact_count_before = int(connection.execute(text(
                "select count(*) from creator_contact_point"
            )).scalar_one())

        for table_name, market_column in MARKET_TABLES:
            if table_name not in existing:
                continue
            connection.execute(text(
                f"delete from {table_name} "
                f"where lower({market_column}) in ('jp','uk','us')"
            ))

        for table_name in ("creator_profile_us", "miss_log_us"):
            if table_name in existing:
                connection.execute(text(f"delete from {table_name}"))

        if "outreach" in existing and "im_conversation" in existing:
            connection.execute(text("""
                delete from outreach item
                where item.send_task_id is null
                  and nullif(btrim(item.conversation_id), '') is not null
                  and not exists (
                      select 1
                      from im_conversation conversation
                      where conversation.conversation_id = item.conversation_id
                        and conversation.bd_market = item.bd_market
                  )
            """))

        if "creator_contact_point" in existing:
            contact_count_after = int(connection.execute(text(
                "select count(*) from creator_contact_point"
            )).scalar_one())
            if contact_count_after != contact_count_before:
                raise RuntimeError(
                    "contact_history_changed_during_market_cleanup:"
                    f"{contact_count_before}:{contact_count_after}"
                )


__all__ = [
    "MARKET_TABLES",
    "RETIRED_MARKETS",
    "apply_mx_only_data_cleanup",
]
