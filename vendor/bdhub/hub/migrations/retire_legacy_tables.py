"""Drop retired 014-era and country-specific compatibility tables."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


RETIRED_TABLES = (
    "campaign",
    "campaign_product",
    "campaign_snapshot",
    "carry_report",
    "discover_fastmoss",
    "mcn_creator_daily",
    "relation",
    "relation_snapshot",
    "sample_record",
    "sample_status_event",
    "creator_profile_history",
    "creator_profile_us",
    "miss_log_us",
)


def apply_retire_legacy_tables(engine: Engine) -> None:
    """Drop the explicit retirement list without CASCADE.

    PostgreSQL therefore fails closed if any unreviewed object still depends on
    one of these tables.
    """
    names = ", ".join(RETIRED_TABLES)
    with engine.begin() as connection:
        connection.execute(text(f"drop table if exists {names}"))


__all__ = ["RETIRED_TABLES", "apply_retire_legacy_tables"]
