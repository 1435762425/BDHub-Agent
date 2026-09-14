"""达人 IM 人工状态与入站消息投影。"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import and_, case, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bdhub.hub.im_markets import get as get_im_market
from bdhub.hub.schema import (
    creator_event,
    creator_im_state,
    im_conversation,
    im_message,
    reply_outbox,
)


VALID_STATUSES = frozenset({
    "pending_reply",
    "processing",
    "replied",
    "rejected",
})

_SHOWCASE_RE = re.compile(r"(vitrina|escaparate|橱窗)", re.I)


class CreatorImStateRepo:
    def __init__(self, engine, *, now=None):
        self.engine = engine
        self.now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _identity_key(identity_key: object) -> str:
        value = str(identity_key or "").strip()
        if not value:
            raise ValueError("identity_key_required")
        return value

    @staticmethod
    def _market(market: object) -> str:
        value = str(market or "").strip().lower()
        try:
            get_im_market(value).require_monitor()
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid_im_market") from None
        return value

    @staticmethod
    def _manual_actor(actor: object) -> str:
        value = str(actor or "").strip()
        if not value or value in {"monitor", "system", "auto"}:
            raise ValueError("manual_actor_required")
        return value

    @staticmethod
    def _event_oec_id(identity_key: str) -> str:
        if not identity_key.startswith("oec:") or not identity_key[4:].strip():
            raise ValueError("invalid_identity_key")
        return identity_key[4:].strip()

    @staticmethod
    def _completion_note(note: object) -> str | None:
        value = str(note or "").strip()
        if len(value) > 1000:
            raise ValueError("completion_note_too_long")
        return value or None

    @staticmethod
    def _queued_outbox(
        connection,
        *,
        market: str,
        oec_id: str,
    ) -> dict | None:
        row = connection.execute(
            select(reply_outbox.c.id)
            .select_from(
                reply_outbox.join(
                    im_conversation,
                    and_(
                        im_conversation.c.conversation_id
                        == reply_outbox.c.conversation_id,
                        im_conversation.c.bd_market
                        == reply_outbox.c.bd_market,
                    ),
                )
            )
            .where(
                reply_outbox.c.bd_market == market,
                reply_outbox.c.status.in_(("queued", "unknown")),
                im_conversation.c.creator_oec_id == oec_id,
            )
            .limit(1)
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    @staticmethod
    def _locked_state(connection, *, market: str, identity_key: str) -> dict:
        row = connection.execute(
            select(creator_im_state).where(
                creator_im_state.c.bd_market == market,
                creator_im_state.c.creator_identity_key == identity_key,
            ).with_for_update()
        ).mappings().one_or_none()
        if row is None:
            raise ValueError("im_state_not_found")
        return dict(row)

    @staticmethod
    def _append_event(
        connection,
        *,
        market: str,
        oec_id: str,
        event_type: str,
        actor: str,
        from_status: str,
        to_status: str,
        payload: dict,
        occurred_at: datetime,
    ) -> str:
        event_id = uuid4().hex
        connection.execute(creator_event.insert().values(
            event_id=event_id,
            bd_market=market,
            oec_id=oec_id,
            event_type=event_type,
            actor=actor,
            from_value=from_status,
            to_value=to_status,
            payload=payload,
            created_at=occurred_at,
        ))
        return event_id

    @staticmethod
    def _update_locked_state(
        connection,
        *,
        market: str,
        identity_key: str,
        status: str,
        actor: str,
        now: datetime,
    ) -> dict:
        row = connection.execute(
            update(creator_im_state)
            .where(
                creator_im_state.c.bd_market == market,
                creator_im_state.c.creator_identity_key == identity_key,
            )
            .values(
                status=status,
                status_set_by=actor,
                status_set_at=now,
                rejected_at=now if status == "rejected" else None,
                updated_at=now,
            )
            .returning(creator_im_state)
        ).mappings().one()
        return dict(row)

    def mark_inbound(
        self,
        *,
        market: str,
        identity_key: str,
        inbound_at: datetime,
        connection=None,
    ) -> dict:
        clean_market = self._market(market)
        clean_identity_key = self._identity_key(identity_key)
        if inbound_at.tzinfo is None:
            raise ValueError("timezone_required")
        values = {
            "bd_market": clean_market,
            "creator_identity_key": clean_identity_key,
            "status": "pending_reply",
            "status_set_by": "monitor",
            "status_set_at": inbound_at,
            "last_inbound_at": inbound_at,
            "rejected_at": None,
            "updated_at": self.now(),
        }

        def execute(con):
            statement = pg_insert(creator_im_state).values(**values)
            incoming = statement.excluded
            current = creator_im_state.c
            is_rejected = current.status == "rejected"
            is_newer = (
                current.last_inbound_at.is_(None)
                | (incoming.last_inbound_at > current.last_inbound_at)
            )
            row = con.execute(
                statement.on_conflict_do_update(
                    index_elements=["bd_market", "creator_identity_key"],
                    set_={
                        "status": case(
                            (is_rejected, "rejected"),
                            (is_newer, "pending_reply"),
                            else_=current.status,
                        ),
                        "status_set_by": case(
                            (
                                is_rejected | ~is_newer,
                                current.status_set_by,
                            ),
                            else_="monitor",
                        ),
                        "status_set_at": case(
                            (
                                is_rejected | ~is_newer,
                                current.status_set_at,
                            ),
                            else_=incoming.status_set_at,
                        ),
                        "last_inbound_at": case(
                            (current.last_inbound_at.is_(None), incoming.last_inbound_at),
                            (incoming.last_inbound_at > current.last_inbound_at, incoming.last_inbound_at),
                            else_=current.last_inbound_at,
                        ),
                        "rejected_at": current.rejected_at,
                        "updated_at": incoming.updated_at,
                    },
                )
                .returning(creator_im_state)
            ).mappings().one()
            return dict(row)

        if connection is not None:
            return execute(connection)
        with self.engine.begin() as own_connection:
            return execute(own_connection)

    def set_manual_status(
        self,
        *,
        market: str,
        identity_key: str,
        status: str,
        actor: str,
        connection=None,
    ) -> dict:
        clean_market = self._market(market)
        clean_actor = self._manual_actor(actor)
        if status not in VALID_STATUSES:
            raise ValueError("invalid_status")
        clean_identity_key = self._identity_key(identity_key)

        now = self.now()
        statement = pg_insert(creator_im_state).values(
            bd_market=clean_market,
            creator_identity_key=clean_identity_key,
            status=status,
            status_set_by=clean_actor,
            status_set_at=now,
            last_inbound_at=None,
            rejected_at=now if status == "rejected" else None,
            updated_at=now,
        )
        def execute(con):
            row = con.execute(
                statement.on_conflict_do_update(
                    index_elements=["bd_market", "creator_identity_key"],
                    set_={
                        "status": status,
                        "status_set_by": clean_actor,
                        "status_set_at": now,
                        "rejected_at": now if status == "rejected" else None,
                        "updated_at": now,
                    },
                )
                .returning(creator_im_state)
            ).mappings().one()
            return dict(row)

        if connection is not None:
            return execute(connection)
        with self.engine.begin() as own_connection:
            return execute(own_connection)

    def mark_seen(
        self,
        *,
        market: str,
        identity_key: str,
        seen_at: datetime,
        actor: str,
        connection=None,
    ) -> dict:
        """单调推进 BD Hub 本地互动已看水位，不触碰平台未读状态。"""
        clean_market = self._market(market)
        clean_identity_key = self._identity_key(identity_key)
        self._manual_actor(actor)
        if seen_at.tzinfo is None:
            raise ValueError("timezone_required")

        def execute(con):
            row = con.execute(
                update(creator_im_state)
                .where(
                    creator_im_state.c.bd_market == clean_market,
                    creator_im_state.c.creator_identity_key == clean_identity_key,
                )
                .values(
                    last_seen_interaction_at=case(
                        (
                            creator_im_state.c.last_seen_interaction_at.is_(None),
                            seen_at,
                        ),
                        (
                            seen_at > creator_im_state.c.last_seen_interaction_at,
                            seen_at,
                        ),
                        else_=creator_im_state.c.last_seen_interaction_at,
                    ),
                    updated_at=self.now(),
                )
                .returning(creator_im_state)
            ).mappings().one_or_none()
            if row is None:
                raise LookupError("im_state_not_found")
            return dict(row)

        if connection is not None:
            return execute(connection)
        with self.engine.begin() as own_connection:
            return execute(own_connection)

    def complete(
        self,
        *,
        market: str,
        identity_key: str,
        actor: str,
        note: str | None = None,
        expected_last_inbound_at: datetime | None = None,
        connection=None,
    ) -> dict:
        """完成人工事项，并在达人事件时间线保留可审计备注。"""
        clean_market = self._market(market)
        clean_identity_key = self._identity_key(identity_key)
        clean_actor = self._manual_actor(actor)
        clean_note = self._completion_note(note)
        oec_id = self._event_oec_id(clean_identity_key)
        if (
            expected_last_inbound_at is not None
            and expected_last_inbound_at.tzinfo is None
        ):
            raise ValueError("timezone_required")

        def execute(con):
            current = self._locked_state(
                con,
                market=clean_market,
                identity_key=clean_identity_key,
            )
            if current["status"] not in {"pending_reply", "processing"}:
                raise ValueError("invalid_completion_state")
            if (
                expected_last_inbound_at is not None
                and current.get("last_inbound_at") != expected_last_inbound_at
            ):
                raise ValueError("stale_inbox_state")
            if self._queued_outbox(
                con,
                market=clean_market,
                oec_id=oec_id,
            ) is not None:
                raise ValueError("outbox_pending")
            now = self.now()
            updated = self._update_locked_state(
                con,
                market=clean_market,
                identity_key=clean_identity_key,
                status="replied",
                actor=clean_actor,
                now=now,
            )
            event_id = self._append_event(
                con,
                market=clean_market,
                oec_id=oec_id,
                event_type="im_status_completed",
                actor=clean_actor,
                from_status=current["status"],
                to_status="replied",
                payload={
                    "identity_key": clean_identity_key,
                    "note": clean_note,
                    "last_inbound_at": (
                        current["last_inbound_at"].isoformat()
                        if current.get("last_inbound_at") is not None
                        else None
                    ),
                },
                occurred_at=now,
            )
            return {
                **updated,
                "event_id": event_id,
                "previous_status": current["status"],
                "note": clean_note,
            }

        if connection is not None:
            return execute(connection)
        with self.engine.begin() as own_connection:
            return execute(own_connection)

    def reopen(
        self,
        *,
        market: str,
        identity_key: str,
        actor: str,
        connection=None,
    ) -> dict:
        """把已完成事项重新放回待处理队列，并保留一次新处理周期。"""
        clean_market = self._market(market)
        clean_identity_key = self._identity_key(identity_key)
        clean_actor = self._manual_actor(actor)
        oec_id = self._event_oec_id(clean_identity_key)

        def execute(con):
            current = self._locked_state(
                con,
                market=clean_market,
                identity_key=clean_identity_key,
            )
            if current["status"] != "replied":
                raise ValueError("invalid_reopen_state")
            now = self.now()
            updated = self._update_locked_state(
                con,
                market=clean_market,
                identity_key=clean_identity_key,
                status="pending_reply",
                actor=clean_actor,
                now=now,
            )
            event_id = self._append_event(
                con,
                market=clean_market,
                oec_id=oec_id,
                event_type="im_status_reopened",
                actor=clean_actor,
                from_status="replied",
                to_status="pending_reply",
                payload={"identity_key": clean_identity_key},
                occurred_at=now,
            )
            return {
                **updated,
                "event_id": event_id,
                "previous_status": "replied",
            }

        if connection is not None:
            return execute(connection)
        with self.engine.begin() as own_connection:
            return execute(own_connection)

    def undo_completion(
        self,
        *,
        market: str,
        identity_key: str,
        event_id: str,
        actor: str,
        connection=None,
    ) -> dict:
        """撤销最近一次完成；新入站或后续操作出现后拒绝旧撤销。"""
        clean_market = self._market(market)
        clean_identity_key = self._identity_key(identity_key)
        clean_actor = self._manual_actor(actor)
        clean_event_id = str(event_id or "").strip()
        if not clean_event_id or len(clean_event_id) > 128:
            raise ValueError("invalid_event_id")
        oec_id = self._event_oec_id(clean_identity_key)

        def execute(con):
            current = self._locked_state(
                con,
                market=clean_market,
                identity_key=clean_identity_key,
            )
            event_row = con.execute(
                select(creator_event).where(
                    creator_event.c.event_id == clean_event_id,
                    creator_event.c.bd_market == clean_market,
                    creator_event.c.oec_id == oec_id,
                    creator_event.c.event_type == "im_status_completed",
                )
            ).mappings().one_or_none()
            if event_row is None:
                raise ValueError("completion_event_not_found")
            event = dict(event_row)
            if (
                current["status"] != "replied"
                or current.get("status_set_at") != event.get("created_at")
            ):
                raise ValueError("completion_not_latest")
            restore_status = str(event.get("from_value") or "")
            if restore_status not in {"pending_reply", "processing"}:
                raise ValueError("invalid_completion_event")
            now = self.now()
            updated = self._update_locked_state(
                con,
                market=clean_market,
                identity_key=clean_identity_key,
                status=restore_status,
                actor=clean_actor,
                now=now,
            )
            undo_event_id = self._append_event(
                con,
                market=clean_market,
                oec_id=oec_id,
                event_type="im_status_completion_undone",
                actor=clean_actor,
                from_status="replied",
                to_status=restore_status,
                payload={
                    "identity_key": clean_identity_key,
                    "reverts_event_id": clean_event_id,
                },
                occurred_at=now,
            )
            return {
                **updated,
                "event_id": undo_event_id,
                "undone_event_id": clean_event_id,
                "previous_status": "replied",
            }

        if connection is not None:
            return execute(connection)
        with self.engine.begin() as own_connection:
            return execute(own_connection)

    def get(
        self,
        *,
        market: str,
        identity_key: str,
        connection=None,
    ) -> dict | None:
        clean_market = self._market(market)
        clean_identity_key = self._identity_key(identity_key)

        def execute(con):
            row = con.execute(
                select(creator_im_state).where(
                    creator_im_state.c.bd_market == clean_market,
                    creator_im_state.c.creator_identity_key == clean_identity_key,
                )
            ).mappings().one_or_none()
            return dict(row) if row is not None else None

        if connection is not None:
            return execute(connection)
        with self.engine.connect() as own_connection:
            return execute(own_connection)

    def is_rejected(
        self,
        *,
        market: str,
        identity_key: str,
        connection=None,
    ) -> bool:
        row = self.get(
            market=market,
            identity_key=identity_key,
            connection=connection,
        )
        return bool(row and row["status"] == "rejected")

    def rejected_identity_keys(self, *, market: str, identity_keys, connection=None) -> set[str]:
        """同一启动事务内分批复核拒收，避免万人名单逐人访问数据库。"""
        clean_market = self._market(market)
        keys = sorted({self._identity_key(key) for key in identity_keys})

        def execute(con):
            rejected = set()
            for offset in range(0, len(keys), 500):
                rejected.update(con.execute(select(creator_im_state.c.creator_identity_key).where(
                    creator_im_state.c.bd_market == clean_market,
                    creator_im_state.c.status == "rejected",
                    creator_im_state.c.creator_identity_key.in_(keys[offset:offset + 500]),
                )).scalars().all())
            return rejected

        if not keys:
            return set()
        if connection is not None:
            return execute(connection)
        with self.engine.connect() as own_connection:
            return execute(own_connection)

    def backfill_unread(
        self,
        *,
        market: str,
        apply: bool = False,
        oec_prefix: str | None = None,
    ) -> dict:
        """把尚无人工状态的未读历史回复补为待回复；默认只预览。"""
        clean_market = self._market(market)
        identity_key = literal("oec:") + im_conversation.c.creator_oec_id
        source = im_conversation.outerjoin(
            creator_im_state,
            (
                creator_im_state.c.bd_market
                == im_conversation.c.bd_market
            )
            & (
                creator_im_state.c.creator_identity_key
                == identity_key
            ),
        )
        conditions = [
            im_conversation.c.bd_market == clean_market,
            func.coalesce(im_conversation.c.unread_count, 0) > 0,
            func.coalesce(
                im_conversation.c.last_creator_reply_ms,
                0,
            ) > 0,
            im_conversation.c.creator_oec_id.is_not(None),
            func.length(
                func.trim(im_conversation.c.creator_oec_id)
            ) > 0,
            creator_im_state.c.creator_identity_key.is_(None),
        ]
        if oec_prefix is not None:
            clean_prefix = str(oec_prefix).strip()
            if not clean_prefix:
                raise ValueError("invalid_oec_prefix")
            conditions.append(
                im_conversation.c.creator_oec_id.like(
                    f"{clean_prefix}%"
                )
            )
        statement = (
            select(
                im_conversation.c.creator_oec_id,
                func.max(
                    im_conversation.c.last_creator_reply_ms
                ).label("last_creator_reply_ms"),
            )
            .select_from(source)
            .where(*conditions)
            .group_by(im_conversation.c.creator_oec_id)
            .order_by(
                func.max(
                    im_conversation.c.last_creator_reply_ms
                ).desc()
            )
        )

        def candidates(connection):
            return connection.execute(statement).mappings().all()

        if not apply:
            with self.engine.connect() as connection:
                rows = candidates(connection)
            return {
                "market": clean_market,
                "candidate_count": len(rows),
                "applied_count": 0,
                "dry_run": True,
            }

        with self.engine.begin() as connection:
            rows = candidates(connection)
            values = []
            now = self.now()
            for row in rows:
                inbound_at = datetime.fromtimestamp(
                    int(row["last_creator_reply_ms"]) / 1000,
                    tz=timezone.utc,
                )
                values.append({
                    "bd_market": clean_market,
                    "creator_identity_key": (
                        f"oec:{str(row['creator_oec_id']).strip()}"
                    ),
                    "status": "pending_reply",
                    "status_set_by": "monitor",
                    "status_set_at": inbound_at,
                    "last_inbound_at": inbound_at,
                    "rejected_at": None,
                    "updated_at": now,
                })
            applied_count = 0
            if values:
                insert = pg_insert(creator_im_state).values(values)
                applied_count = len(connection.execute(
                    insert.on_conflict_do_nothing(
                        index_elements=[
                            "bd_market",
                            "creator_identity_key",
                        ]
                    ).returning(
                        creator_im_state.c.creator_identity_key
                    )
                ).all())
        return {
            "market": clean_market,
            "candidate_count": len(rows),
            "applied_count": applied_count,
            "dry_run": False,
        }

    def backfill_interactions(
        self,
        *,
        market: str,
        apply: bool = False,
        oec_prefix: str | None = None,
    ) -> dict:
        """把全部历史达人回复/加橱窗补进人工队列；默认只预览。

        会话汇总会保留已经不在当前消息窗口内的历史事实，消息表则补足
        偶发的汇总缺口。只插入尚无 ``creator_im_state`` 的 OEC，绝不
        覆盖人工处理中、已完成、已拒绝或监听已经建立的状态。
        """
        clean_market = self._market(market)
        clean_prefix = None
        if oec_prefix is not None:
            clean_prefix = str(oec_prefix).strip()
            if not clean_prefix:
                raise ValueError("invalid_oec_prefix")

        def recoverable_conversations(connection):
            """从消息恢复缺失会话，或补齐同市场会话缺失的 OEC。"""
            missing = im_message.outerjoin(
                im_conversation,
                and_(
                    im_conversation.c.bd_market == im_message.c.bd_market,
                    im_conversation.c.conversation_id
                    == im_message.c.conversation_id,
                ),
            )
            message_rows = connection.execute(
                select(
                    im_message.c.server_id,
                    im_message.c.conversation_id,
                    im_message.c.creator_oec_id,
                    im_message.c.is_from_me,
                    im_message.c.sender_role,
                    im_message.c.content,
                    im_message.c.create_ms,
                    im_message.c.captured_at,
                )
                .select_from(missing)
                .where(
                    im_message.c.bd_market == clean_market,
                    im_message.c.conversation_id.is_not(None),
                    func.length(func.trim(
                        im_message.c.conversation_id
                    )) > 0,
                    or_(
                        im_conversation.c.conversation_id.is_(None),
                        im_conversation.c.creator_oec_id.is_(None),
                        func.length(func.trim(
                            im_conversation.c.creator_oec_id
                        )) == 0,
                    ),
                )
                .order_by(
                    im_message.c.conversation_id,
                    im_message.c.create_ms,
                    im_message.c.server_id,
                )
            ).mappings().all()
            grouped: dict[str, list] = {}
            for row in message_rows:
                conversation_id = str(
                    row["conversation_id"] or ""
                ).strip()
                grouped.setdefault(conversation_id, []).append(row)

            recovered = []
            for conversation_id, messages in grouped.items():
                oec_ids = {
                    str(row["creator_oec_id"] or "").strip()
                    for row in messages
                    if str(row["creator_oec_id"] or "").strip()
                }
                # 会话只能绑定一个稳定 OEC；缺失或冲突时保持 fail-closed。
                if len(oec_ids) != 1:
                    continue
                creator_oec_id = next(iter(oec_ids))
                if (
                    clean_prefix is not None
                    and not creator_oec_id.startswith(clean_prefix)
                ):
                    continue

                def event_ms(row) -> int:
                    value = int(row["create_ms"] or 0)
                    if value > 0:
                        return value
                    captured_at = row["captured_at"]
                    if captured_at is not None:
                        return int(captured_at.timestamp() * 1000)
                    return 1

                creator = [
                    row for row in messages
                    if not bool(row["is_from_me"])
                    and int(row["sender_role"] or 0) == 1
                ]
                mine = [row for row in messages if bool(row["is_from_me"])]
                showcase = [
                    row for row in messages
                    if not bool(row["is_from_me"])
                    and int(row["sender_role"] or 0) == 3
                    and _SHOWCASE_RE.search(str(row["content"] or ""))
                ]
                if not creator and not showcase:
                    continue

                last_creator = max(
                    creator,
                    key=lambda row: (event_ms(row), row["server_id"]),
                    default=None,
                )
                showcase_ms = max(
                    (event_ms(row) for row in showcase),
                    default=0,
                )
                captured_values = [
                    row["captured_at"]
                    for row in messages
                    if row["captured_at"] is not None
                ]
                recovered.append({
                    "conversation_id": conversation_id,
                    "bd_market": clean_market,
                    "creator_oec_id": creator_oec_id,
                    # 平台 unread_count 不可由历史消息精确反推。
                    "unread_count": None,
                    "last_active_ms": max(event_ms(row) for row in messages),
                    "creator_reply_count": len(creator),
                    "last_creator_reply": (
                        last_creator["content"]
                        if last_creator is not None else None
                    ),
                    "last_creator_reply_ms": (
                        event_ms(last_creator)
                        if last_creator is not None else None
                    ),
                    "last_mine_ms": max(
                        (event_ms(row) for row in mine),
                        default=None,
                    ),
                    "showcase_at": (
                        datetime.fromtimestamp(
                            showcase_ms / 1000,
                            tz=timezone.utc,
                        )
                        if showcase_ms else None
                    ),
                    "showcase_count": len(showcase),
                    "raw_json": None,
                    "captured_at": (
                        max(captured_values) if captured_values else self.now()
                    ),
                })
            return recovered

        conversation_oec = func.trim(im_conversation.c.creator_oec_id)
        conversation_reply = or_(
            func.coalesce(im_conversation.c.creator_reply_count, 0) > 0,
            func.coalesce(im_conversation.c.last_creator_reply_ms, 0) > 0,
        )
        conversation_showcase = or_(
            im_conversation.c.showcase_at.is_not(None),
            func.coalesce(im_conversation.c.showcase_count, 0) > 0,
        )
        conversation_fallback_ms = func.coalesce(
            func.nullif(im_conversation.c.last_active_ms, 0),
            func.extract("epoch", im_conversation.c.captured_at) * 1000,
            1,
        )
        conversation_rows = (
            select(
                im_conversation.c.bd_market.label("bd_market"),
                conversation_oec.label("creator_oec_id"),
                func.max(
                    case((conversation_reply, 1), else_=0)
                ).label("has_reply"),
                func.max(
                    case((conversation_showcase, 1), else_=0)
                ).label("has_showcase"),
                func.max(case(
                    (
                        conversation_reply,
                        func.coalesce(
                            func.nullif(
                                im_conversation.c.last_creator_reply_ms,
                                0,
                            ),
                            conversation_fallback_ms,
                        ),
                    ),
                    else_=None,
                )).label("reply_ms"),
                func.max(case(
                    (
                        conversation_showcase,
                        func.coalesce(
                            func.extract(
                                "epoch",
                                im_conversation.c.showcase_at,
                            )
                            * 1000,
                            conversation_fallback_ms,
                        ),
                    ),
                    else_=None,
                )).label("showcase_ms"),
            )
            .where(
                im_conversation.c.bd_market == clean_market,
                im_conversation.c.creator_oec_id.is_not(None),
                func.length(conversation_oec) > 0,
                or_(conversation_reply, conversation_showcase),
            )
            .group_by(
                im_conversation.c.bd_market,
                conversation_oec,
            )
        )

        message_join = im_message.outerjoin(
            im_conversation,
            and_(
                im_conversation.c.bd_market == im_message.c.bd_market,
                im_conversation.c.conversation_id
                == im_message.c.conversation_id,
            ),
        )
        message_oec = func.coalesce(
            func.nullif(func.trim(im_message.c.creator_oec_id), ""),
            func.nullif(conversation_oec, ""),
        )
        message_inbound = func.coalesce(
            im_message.c.is_from_me,
            False,
        ).is_(False)
        message_reply = and_(
            message_inbound,
            im_message.c.sender_role == 1,
        )
        message_showcase = and_(
            message_inbound,
            im_message.c.sender_role == 3,
            func.coalesce(im_message.c.content, "").op("~*")(
                r"(vitrina|escaparate|橱窗)"
            ),
        )
        message_event_ms = func.coalesce(
            func.nullif(im_message.c.create_ms, 0),
            func.extract("epoch", im_message.c.captured_at) * 1000,
            1,
        )
        message_rows = (
            select(
                im_message.c.bd_market.label("bd_market"),
                message_oec.label("creator_oec_id"),
                func.max(case((message_reply, 1), else_=0)).label(
                    "has_reply"
                ),
                func.max(case((message_showcase, 1), else_=0)).label(
                    "has_showcase"
                ),
                func.max(case(
                    (message_reply, message_event_ms),
                    else_=None,
                )).label("reply_ms"),
                func.max(case(
                    (message_showcase, message_event_ms),
                    else_=None,
                )).label("showcase_ms"),
            )
            .select_from(message_join)
            .where(
                im_message.c.bd_market == clean_market,
                message_oec.is_not(None),
                func.length(message_oec) > 0,
                or_(message_reply, message_showcase),
            )
            .group_by(im_message.c.bd_market, message_oec)
        )

        combined = conversation_rows.union_all(message_rows).subquery(
            "im_interaction_backfill_source"
        )
        facts = (
            select(
                combined.c.bd_market,
                combined.c.creator_oec_id,
                func.max(combined.c.has_reply).label("has_reply"),
                func.max(combined.c.has_showcase).label("has_showcase"),
                func.max(combined.c.reply_ms).label("reply_ms"),
                func.max(combined.c.showcase_ms).label("showcase_ms"),
            )
            .group_by(
                combined.c.bd_market,
                combined.c.creator_oec_id,
            )
            .subquery("im_interaction_backfill_facts")
        )
        identity_key = literal("oec:") + facts.c.creator_oec_id
        source = facts.outerjoin(
            creator_im_state,
            and_(
                creator_im_state.c.bd_market == facts.c.bd_market,
                creator_im_state.c.creator_identity_key == identity_key,
            ),
        )
        conditions = [
            facts.c.bd_market == clean_market,
            or_(facts.c.has_reply > 0, facts.c.has_showcase > 0),
            creator_im_state.c.creator_identity_key.is_(None),
        ]
        if clean_prefix is not None:
            conditions.append(
                facts.c.creator_oec_id.like(f"{clean_prefix}%")
            )
        statement = (
            select(
                facts.c.creator_oec_id,
                facts.c.has_reply,
                facts.c.has_showcase,
                facts.c.reply_ms,
                facts.c.showcase_ms,
            )
            .select_from(source)
            .where(*conditions)
            .order_by(
                func.greatest(
                    func.coalesce(facts.c.reply_ms, 0),
                    func.coalesce(facts.c.showcase_ms, 0),
                ).desc(),
                facts.c.creator_oec_id,
            )
        )

        def candidates(connection):
            return connection.execute(statement).mappings().all()

        def result(
            rows,
            *,
            recoverable_count: int,
            recovered_count: int,
            applied_count: int,
            dry_run: bool,
        ):
            reply_only = sum(
                bool(row["has_reply"]) and not bool(row["has_showcase"])
                for row in rows
            )
            showcase_only = sum(
                bool(row["has_showcase"]) and not bool(row["has_reply"])
                for row in rows
            )
            both = sum(
                bool(row["has_reply"]) and bool(row["has_showcase"])
                for row in rows
            )
            return {
                "market": clean_market,
                "candidate_count": len(rows),
                "reply_only_count": reply_only,
                "showcase_only_count": showcase_only,
                "both_count": both,
                "recoverable_conversation_count": recoverable_count,
                "recovered_conversation_count": recovered_count,
                "applied_count": applied_count,
                "dry_run": dry_run,
            }

        if not apply:
            with self.engine.connect() as connection:
                rows = candidates(connection)
                recoveries = recoverable_conversations(connection)
            return result(
                rows,
                recoverable_count=len(recoveries),
                recovered_count=0,
                applied_count=0,
                dry_run=True,
            )

        with self.engine.begin() as connection:
            rows = candidates(connection)
            recoveries = recoverable_conversations(connection)
            recovered_count = 0
            if recoveries:
                insert = pg_insert(im_conversation).values(recoveries)
                recovered_count = len(connection.execute(
                    insert.on_conflict_do_update(
                        index_elements=["conversation_id"],
                        set_={
                            "creator_oec_id": insert.excluded.creator_oec_id,
                        },
                        where=and_(
                            im_conversation.c.bd_market == clean_market,
                            or_(
                                im_conversation.c.creator_oec_id.is_(None),
                                func.length(func.trim(
                                    im_conversation.c.creator_oec_id
                                )) == 0,
                            ),
                        ),
                    ).returning(im_conversation.c.conversation_id)
                ).all())
            now = self.now()
            values = []
            for row in rows:
                event_ms = max(
                    int(row["reply_ms"] or 0),
                    int(row["showcase_ms"] or 0),
                    1,
                )
                inbound_at = datetime.fromtimestamp(
                    event_ms / 1000,
                    tz=timezone.utc,
                )
                values.append({
                    "bd_market": clean_market,
                    "creator_identity_key": (
                        f"oec:{str(row['creator_oec_id']).strip()}"
                    ),
                    "status": "pending_reply",
                    "status_set_by": "monitor",
                    "status_set_at": inbound_at,
                    "last_inbound_at": inbound_at,
                    "rejected_at": None,
                    "updated_at": now,
                })
            applied_count = 0
            for start in range(0, len(values), 500):
                insert = pg_insert(creator_im_state).values(
                    values[start:start + 500]
                )
                applied_count += len(connection.execute(
                    insert.on_conflict_do_nothing(
                        index_elements=[
                            "bd_market",
                            "creator_identity_key",
                        ]
                    ).returning(
                        creator_im_state.c.creator_identity_key
                    )
                ).all())
        return result(
            rows,
            recoverable_count=len(recoveries),
            recovered_count=recovered_count,
            applied_count=applied_count,
            dry_run=False,
        )


__all__ = ["CreatorImStateRepo", "VALID_STATUSES"]
