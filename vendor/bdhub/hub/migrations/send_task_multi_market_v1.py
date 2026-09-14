"""把发送任务市场约束扩展到 Dashboard 七国目录。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


SEND_TASK_MARKETS = ("mx", "br", "uk", "it", "jp", "us", "de")
_MARKET_SQL = ",".join(f"'{market}'" for market in SEND_TASK_MARKETS)


def apply_send_task_multi_market_v1(engine: Engine) -> None:
    """显式替换 ``send_task`` 市场约束；SQLite 测试环境安全跳过。

    本迁移只放宽数据模型，不代表任一非 MX 市场已获得发送授权。运行时仍由
    市场能力注册表、preflight 和显式 canary 门禁控制。
    """
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        connection.execute(text(
            "alter table send_task "
            "drop constraint if exists ck_send_task_market"
        ))
        connection.execute(text(
            "alter table send_task "
            "add constraint ck_send_task_market "
            f"check (bd_market in ({_MARKET_SQL})) not valid"
        ))
        connection.execute(text(
            "alter table send_task "
            "validate constraint ck_send_task_market"
        ))


__all__ = ["SEND_TASK_MARKETS", "apply_send_task_multi_market_v1"]
