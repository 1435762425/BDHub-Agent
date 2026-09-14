"""报告分析 v5：为分类导入预览补充“已修改”状态。"""
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
            'skipped_blank',
            'invalid'
        )
    )
""")


def apply_report_analysis_v5(engine: Engine) -> None:
    """只替换既有检查约束，不读写任何业务行。"""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        if not inspect(connection).has_table(_TABLE_NAME):
            return
        connection.execute(_DROP_CONSTRAINT)
        connection.execute(_ADD_CONSTRAINT)


__all__ = ["apply_report_analysis_v5"]
