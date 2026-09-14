"""Report analysis v7: persist the account bound to each sync run."""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def apply_report_analysis_v7(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        if not inspect(connection).has_table("report_sync_run"):
            return
        connection.execute(text(
            "alter table report_sync_run "
            "add column if not exists account_name text"
        ))


__all__ = ["apply_report_analysis_v7"]
