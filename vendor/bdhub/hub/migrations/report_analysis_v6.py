"""报告分析 v6：分类导入允许记录非阻断的未匹配行。"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


_TABLE_NAME = "classification_import_row"
_DROP_CONSTRAINT = text(
    "alter table classification_import_row "
    "drop constraint if exists ck_classification_import_row_status"
)
_ADD_CONSTRAINT = text("""
    alter table classification_import_row
    add constraint ck_classification_import_row_status check (
        status in (
            'matched_new',
            'matched_changed',
            'matched_handle_oec_unverified',
            'unchanged',
            'conflict',
            'identity_ambiguous',
            'unknown_creator',
            'unknown_classification',
            'duplicate_same',
            'duplicate_conflict',
            'skipped_unmatched',
            'skipped_blank',
            'invalid'
        )
    )
""")


def apply_report_analysis_v6(engine: Engine) -> None:
    """只扩展预览状态约束，不读写报告或达人归属业务数据。"""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        if not inspect(connection).has_table(_TABLE_NAME):
            return
        connection.execute(_DROP_CONSTRAINT)
        connection.execute(_ADD_CONSTRAINT)


__all__ = ["apply_report_analysis_v6"]
