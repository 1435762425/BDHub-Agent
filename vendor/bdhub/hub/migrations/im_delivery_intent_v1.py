"""v63：人工与AI发送持久化意图，保留原批量任务作为自己的唯一真相源。"""
from sqlalchemy.engine import Engine
from sqlalchemy import text


def apply_im_delivery_intent_v1(engine: Engine) -> None:
    from bdhub.hub.schema import im_delivery_intent
    im_delivery_intent.create(engine, checkfirst=True)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE reply_outbox ADD COLUMN IF NOT EXISTS retry_no integer NOT NULL DEFAULT 0"))
