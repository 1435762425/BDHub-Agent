"""发送任务商品卡的归因占位与状态生命周期。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import and_, case, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from bdhub.hub.market_catalog import get as get_dashboard_market
from bdhub.hub.schema import (
    creator_im_state,
    im_conversation,
    partner_attribution_daily,
    report_file,
    send_task,
    send_task_attribution,
    send_task_component,
    send_task_lead,
    tap_creator_product_history,
)
from bdhub.send.attribution import window_for_card_sent_at
from bdhub.send.attribution_settlement import (
    decide_attribution_outcome,
    settle_commerce_evidence,
)


_OPEN_STATUSES = frozenset({"observing", "data_incomplete"})
_RECIPIENT_STATUSES = frozenset({
    "observing",
    "data_incomplete",
    "matched",
    "not_matched",
})


class ActiveAttributionConflict(RuntimeError):
    """另一个任务已经持有同一达人+PID 的有效观察期。"""


class SendAttributionRepo:
    def __init__(self, engine, *, now=None, id_factory=None):
        self.engine = engine
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.id_factory = id_factory or (
            lambda _kind: f"attribution_{uuid4().hex}"
        )

    @staticmethod
    def _lead_market(connection, lead_id: str) -> str:
        return str(connection.execute(
            select(send_task.c.bd_market)
            .select_from(
                send_task_lead.join(
                    send_task,
                    send_task.c.task_id == send_task_lead.c.task_id,
                )
            )
            .where(send_task_lead.c.lead_id == lead_id)
        ).scalar_one())

    @staticmethod
    def _commerce_per_pair():
        """任务内达人+PID 去重后，读取 current+sealed TAP 成交事实。"""
        pair_window = (
            select(
                send_task_attribution.c.task_id,
                send_task_attribution.c.bd_market,
                send_task_attribution.c.creator_identity_key,
                send_task_attribution.c.pid,
                func.min(send_task_attribution.c.window_start_date).label(
                    "window_start_date"
                ),
                func.max(send_task_attribution.c.window_end_date).label(
                    "window_end_date"
                ),
            )
            .where(send_task_attribution.c.status == "matched")
            .group_by(
                send_task_attribution.c.task_id,
                send_task_attribution.c.bd_market,
                send_task_attribution.c.creator_identity_key,
                send_task_attribution.c.pid,
            )
            .subquery("send_attribution_pair_window")
        )
        current_fact = report_file.c.file_id.is_not(None)
        joined = (
            pair_window.outerjoin(
                partner_attribution_daily,
                and_(
                    partner_attribution_daily.c.market
                    == pair_window.c.bd_market,
                    partner_attribution_daily.c.report_kind == "tap",
                    partner_attribution_daily.c.creator_identity_key
                    == pair_window.c.creator_identity_key,
                    partner_attribution_daily.c.product_id
                    == pair_window.c.pid,
                    partner_attribution_daily.c.report_date
                    >= pair_window.c.window_start_date,
                    partner_attribution_daily.c.report_date
                    <= pair_window.c.window_end_date,
                ),
            ).outerjoin(
                report_file,
                and_(
                    report_file.c.file_id
                    == partner_attribution_daily.c.file_id,
                    report_file.c.report_date
                    == partner_attribution_daily.c.report_date,
                    report_file.c.market == pair_window.c.bd_market,
                    report_file.c.report_kind == "tap",
                    report_file.c.status == "sealed",
                    report_file.c.is_current.is_(True),
                ),
            )
        )
        return (
            select(
                pair_window.c.task_id,
                pair_window.c.creator_identity_key,
                pair_window.c.pid,
                func.sum(
                    case(
                        (
                            current_fact,
                            partner_attribution_daily.c.creator_attributed_gmv,
                        ),
                        else_=None,
                    )
                ).label("attributed_gmv"),
                func.sum(
                    case(
                        (
                            current_fact,
                            partner_attribution_daily.c.creator_attributed_orders,
                        ),
                        else_=None,
                    )
                ).label("attributed_orders"),
            )
            .select_from(joined)
            .group_by(
                pair_window.c.task_id,
                pair_window.c.creator_identity_key,
                pair_window.c.pid,
            )
            .subquery("send_attribution_commerce_pair")
        )

    @classmethod
    def _commerce_task_statement(cls, *, task_id: str):
        pair = cls._commerce_per_pair()
        return (
            select(
                func.count().label("commerce_matched_pairs"),
                func.count()
                .filter(pair.c.attributed_gmv.is_not(None))
                .label("gmv_evidence_pairs"),
                func.count()
                .filter(pair.c.attributed_orders.is_not(None))
                .label("order_evidence_pairs"),
                func.sum(pair.c.attributed_gmv).label("attributed_gmv"),
                func.sum(pair.c.attributed_orders).label(
                    "attributed_orders"
                ),
            )
            .select_from(pair)
            .where(pair.c.task_id == task_id)
        )

    @classmethod
    def _commerce_per_creator(cls):
        pair = cls._commerce_per_pair()
        return (
            select(
                pair.c.task_id,
                pair.c.creator_identity_key,
                func.count().label("commerce_matched_pairs"),
                func.count()
                .filter(pair.c.attributed_gmv.is_not(None))
                .label("gmv_evidence_pairs"),
                func.count()
                .filter(pair.c.attributed_orders.is_not(None))
                .label("order_evidence_pairs"),
                func.sum(pair.c.attributed_gmv).label("attributed_gmv"),
                func.sum(pair.c.attributed_orders).label(
                    "attributed_orders"
                ),
            )
            .select_from(pair)
            .group_by(pair.c.task_id, pair.c.creator_identity_key)
            .subquery("send_attribution_commerce_creator")
        )

    @staticmethod
    def _commerce_decision(row):
        orders = row.get("attributed_orders")
        return settle_commerce_evidence(
            matched_pairs=int(row.get("commerce_matched_pairs") or 0),
            gmv_evidence_pairs=int(row.get("gmv_evidence_pairs") or 0),
            order_evidence_pairs=int(row.get("order_evidence_pairs") or 0),
            attributed_gmv=row.get("attributed_gmv"),
            attributed_orders=(int(orders) if orders is not None else None),
        )

    @staticmethod
    def _creator_task_signals():
        latest_reply = (
            select(
                im_conversation.c.bd_market,
                im_conversation.c.creator_oec_id,
                func.max(im_conversation.c.last_creator_reply_ms).label(
                    "last_creator_reply_ms"
                ),
            )
            .where(im_conversation.c.creator_oec_id.is_not(None))
            .group_by(
                im_conversation.c.bd_market,
                im_conversation.c.creator_oec_id,
            )
            .subquery("send_attribution_latest_creator_reply")
        )
        joined = (
            send_task_lead.join(
                send_task,
                send_task.c.task_id == send_task_lead.c.task_id,
            )
            .outerjoin(
                send_task_component,
                send_task_component.c.lead_id == send_task_lead.c.lead_id,
            )
            .outerjoin(
                latest_reply,
                and_(
                    latest_reply.c.bd_market == send_task.c.bd_market,
                    latest_reply.c.creator_oec_id == send_task_lead.c.oec_id,
                ),
            )
            .outerjoin(
                creator_im_state,
                and_(
                    creator_im_state.c.bd_market == send_task.c.bd_market,
                    creator_im_state.c.creator_identity_key
                    == send_task_lead.c.creator_identity_key,
                ),
            )
        )
        first_sent_at = func.min(send_task_component.c.sent_at).filter(
            send_task_component.c.status == "sent"
        )
        last_reply_ms = func.max(latest_reply.c.last_creator_reply_ms)
        associated = and_(
            first_sent_at.is_not(None),
            last_reply_ms.is_not(None),
            last_reply_ms >= func.extract("epoch", first_sent_at) * 1000,
        )
        return (
            select(
                send_task_lead.c.task_id,
                send_task_lead.c.creator_identity_key,
                case((associated, True), else_=False).label(
                    "associated_reply"
                ),
                case((associated, last_reply_ms), else_=None).label(
                    "associated_reply_at_ms"
                ),
                func.max(creator_im_state.c.status).label(
                    "reply_work_state"
                ),
                case(
                    (
                        func.count().filter(
                            send_task_lead.c.manual_excluded.is_(True)
                        )
                        > 0,
                        True,
                    ),
                    else_=False,
                ).label("manual_excluded"),
            )
            .select_from(joined)
            .where(send_task_lead.c.creator_identity_key.is_not(None))
            .group_by(
                send_task_lead.c.task_id,
                send_task_lead.c.creator_identity_key,
            )
            .subquery("send_attribution_creator_task_signals")
        )

    def reserve_task(self, *, task_id: str, connection=None) -> dict:
        """为任务中具备归因资格的达人+PID 预留唯一观察权。"""

        def execute(con) -> dict:
            task = con.execute(
                select(send_task)
                .where(send_task.c.task_id == task_id)
                .with_for_update()
            ).mappings().one()
            if not get_dashboard_market(
                str(task["bd_market"])
            ).reports_enabled:
                return {
                    "reserved": 0,
                    "preexisting": 0,
                    "ineligible": 0,
                    "state": "reports_disabled",
                }
            if task["send_mode"] == "text_only":
                return {"reserved": 0, "preexisting": 0, "ineligible": 0}

            existing = con.execute(
                select(
                    send_task_attribution.c.status,
                    func.count().label("count"),
                )
                .where(send_task_attribution.c.task_id == task_id)
                .group_by(send_task_attribution.c.status)
            ).mappings().all()
            if existing:
                found = {row["status"]: int(row["count"]) for row in existing}
                return {
                    "reserved": found.get("reserved", 0),
                    "preexisting": found.get("preexisting", 0),
                    "ineligible": found.get("ineligible", 0),
                }

            history = tap_creator_product_history.alias("history")
            leads = con.execute(
                select(send_task_lead, history.c.pid.label("seen_pid"))
                .select_from(
                    send_task_lead.outerjoin(
                        history,
                        and_(
                            history.c.bd_market == "mx",
                            history.c.creator_identity_key
                            == send_task_lead.c.creator_identity_key,
                            history.c.pid == send_task_lead.c.pid,
                        ),
                    )
                )
                .where(
                    send_task_lead.c.task_id == task_id,
                    send_task_lead.c.eligibility_status.in_(["eligible", "warning"]),
                    send_task_lead.c.manual_excluded.is_(False),
                    send_task_lead.c.creator_identity_key.is_not(None),
                    send_task_lead.c.pid.is_not(None),
                )
            ).mappings().all()
            counts = {"reserved": 0, "preexisting": 0, "ineligible": 0}
            rows = []
            now = self.now()
            for lead in leads:
                snapshot = lead["source_snapshot"] or {}
                allowed = set(snapshot.get("allowed_components") or ())
                seen = lead["seen_pid"] is not None
                if "card" not in allowed and not seen:
                    continue
                forced = bool(snapshot.get("force_card"))
                if forced and not str(snapshot.get("force_card_reason") or "").strip():
                    raise ValueError("force_card_reason_required")
                status = (
                    "ineligible"
                    if seen and forced
                    else "preexisting"
                    if seen
                    else "reserved"
                )
                rows.append(
                    {
                        "attribution_id": self.id_factory("attribution"),
                        "task_id": task_id,
                        "lead_id": lead["lead_id"],
                        "bd_market": "mx",
                        "creator_identity_key": lead["creator_identity_key"],
                        "pid": lead["pid"],
                        "status": status,
                        "ineligible_reason": (
                            "forced_card_preexisting"
                            if seen and forced
                            else "preexisting_pair"
                            if seen
                            else None
                        ),
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                counts[status] += 1
            try:
                if rows:
                    con.execute(insert(send_task_attribution), rows)
            except IntegrityError as exc:
                raise ActiveAttributionConflict("active_pair_observation") from exc
            return counts

        if connection is not None:
            return execute(connection)
        with self.engine.begin() as own_connection:
            return execute(own_connection)

    def activate_card_success(
        self,
        *,
        lead_id: str,
        sent_at: datetime,
        connection=None,
    ) -> dict:
        """仅卡片确实发出后，才进入七天观察窗口。"""
        window = window_for_card_sent_at(sent_at)

        def execute(con) -> dict:
            task_market = self._lead_market(con, lead_id)
            if not get_dashboard_market(task_market).reports_enabled:
                return {
                    "lead_id": lead_id,
                    "status": "reports_disabled",
                }
            changed = con.execute(
                update(send_task_attribution)
                .where(
                    send_task_attribution.c.lead_id == lead_id,
                    send_task_attribution.c.status == "reserved",
                )
                .values(
                    status="observing",
                    card_sent_at=sent_at,
                    window_start_date=window.start,
                    window_end_date=window.end,
                    updated_at=self.now(),
                )
                .returning(send_task_attribution)
            ).mappings().one_or_none()
            if changed is not None:
                return dict(changed)
            existing = con.execute(
                select(send_task_attribution).where(
                    send_task_attribution.c.lead_id == lead_id
                )
            ).mappings().one()
            return dict(existing)

        if connection is not None:
            return execute(connection)
        with self.engine.begin() as own_connection:
            return execute(own_connection)

    def release_unsent(
        self,
        *,
        lead_id: str,
        reason: str,
        connection=None,
    ) -> dict:
        """卡片未送达时释放观察资格，不把它计入归因分母。"""
        clean_reason = str(reason or "").strip()
        if not clean_reason:
            raise ValueError("release_reason_required")

        def execute(con) -> dict | None:
            task_market = self._lead_market(con, lead_id)
            if not get_dashboard_market(task_market).reports_enabled:
                return {
                    "lead_id": lead_id,
                    "status": "reports_disabled",
                }
            row = con.execute(
                update(send_task_attribution)
                .where(
                    send_task_attribution.c.lead_id == lead_id,
                    send_task_attribution.c.status == "reserved",
                )
                .values(
                    status="ineligible",
                    ineligible_reason=clean_reason,
                    finalized_at=self.now(),
                    updated_at=self.now(),
                )
                .returning(send_task_attribution)
            ).mappings().one_or_none()
            return dict(row) if row is not None else None

        if connection is not None:
            return execute(connection)
        with self.engine.begin() as own_connection:
            return execute(own_connection)

    @staticmethod
    def _empty_settlement() -> dict:
        return {
            "processed": 0,
            "changed": 0,
            "matched": 0,
            "not_matched": 0,
            "data_incomplete": 0,
            "ineligible": 0,
            "observing": 0,
        }

    def settle_open(
        self,
        *,
        market: str = "mx",
        connection=None,
    ) -> dict:
        """结算全部开放观察；重复运行保持幂等。"""
        clean_market = str(market or "").strip().lower()
        if clean_market != "mx":
            raise ValueError("unsupported_market")

        history = tap_creator_product_history.alias("tap_history")
        joined = send_task_attribution.outerjoin(
            history,
            and_(
                history.c.bd_market
                == send_task_attribution.c.bd_market,
                history.c.creator_identity_key
                == send_task_attribution.c.creator_identity_key,
                history.c.pid == send_task_attribution.c.pid,
            ),
        )

        def execute(con) -> dict:
            candidates = con.execute(
                select(
                    send_task_attribution.c.attribution_id,
                    send_task_attribution.c.status,
                    send_task_attribution.c.window_start_date,
                    send_task_attribution.c.window_end_date,
                    history.c.first_seen_report_date,
                    history.c.first_source_file_id,
                )
                .select_from(joined)
                .where(
                    send_task_attribution.c.bd_market == clean_market,
                    send_task_attribution.c.status.in_(_OPEN_STATUSES),
                )
                .order_by(send_task_attribution.c.attribution_id)
                .with_for_update(of=send_task_attribution)
            ).mappings().all()
            if not candidates:
                return self._empty_settlement()

            sealed_dates = {
                row["report_date"]
                for row in con.execute(
                    select(report_file.c.report_date)
                    .where(
                        report_file.c.market == clean_market,
                        report_file.c.report_kind == "tap",
                        report_file.c.status == "sealed",
                        report_file.c.is_current.is_(True),
                    )
                    .order_by(report_file.c.report_date)
                ).mappings().all()
            }
            result = self._empty_settlement()
            result["processed"] = len(candidates)
            now = self.now()
            for candidate in candidates:
                decision = decide_attribution_outcome(
                    window_start_date=candidate["window_start_date"],
                    window_end_date=candidate["window_end_date"],
                    first_seen_report_date=(
                        candidate.get("first_seen_report_date")
                    ),
                    sealed_report_dates=sealed_dates,
                )
                next_status = decision.status
                if (
                    candidate["status"] == "data_incomplete"
                    and next_status == "observing"
                ):
                    next_status = "data_incomplete"
                result[next_status] += 1
                if next_status == candidate["status"]:
                    continue

                values = {
                    "status": next_status,
                    "updated_at": now,
                }
                if next_status == "matched":
                    values.update({
                        "matched_report_date": (
                            decision.matched_report_date
                        ),
                        "matched_file_id": candidate.get(
                            "first_source_file_id"
                        ),
                        "finalized_at": now,
                        "ineligible_reason": None,
                    })
                elif next_status == "ineligible":
                    values.update({
                        "matched_report_date": None,
                        "matched_file_id": None,
                        "finalized_at": now,
                        "ineligible_reason": decision.reason,
                    })
                elif next_status == "not_matched":
                    values.update({
                        "matched_report_date": None,
                        "matched_file_id": None,
                        "finalized_at": now,
                        "ineligible_reason": None,
                    })
                else:
                    values.update({
                        "finalized_at": None,
                        "ineligible_reason": None,
                    })
                con.execute(
                    update(send_task_attribution)
                    .where(
                        send_task_attribution.c.attribution_id
                        == candidate["attribution_id"],
                        send_task_attribution.c.status.in_(_OPEN_STATUSES),
                    )
                    .values(**values)
                )
                result["changed"] += 1
            return result

        if connection is not None:
            return execute(connection)
        with self.engine.begin() as own_connection:
            return execute(own_connection)

    def settle_tap_file(
        self,
        file_id: str,
        *,
        connection=None,
    ) -> dict:
        """只接受 current+sealed TAP 文件，并结算同市场开放观察。"""
        clean_file_id = str(file_id or "").strip()
        if not clean_file_id:
            raise ValueError("file_id_required")

        def execute(con) -> dict:
            file_row = con.execute(
                select(
                    report_file.c.file_id,
                    report_file.c.market,
                )
                .where(
                    report_file.c.file_id == clean_file_id,
                    report_file.c.market == "mx",
                    report_file.c.report_kind == "tap",
                    report_file.c.status == "sealed",
                    report_file.c.is_current.is_(True),
                )
                .with_for_update()
            ).mappings().one_or_none()
            if file_row is None:
                raise ValueError(
                    "tap_current_sealed_file_required"
                )
            return self.settle_open(
                market=file_row["market"],
                connection=con,
            )

        if connection is not None:
            return execute(connection)
        with self.engine.begin() as own_connection:
            return execute(own_connection)

    @staticmethod
    def _empty_task_stats(*, state: str) -> dict:
        return {
            "state": state,
            "card_recipient_creators": 0,
            "matched_creators": 0,
            "matched_pairs": 0,
            "distinct_pids": 0,
            "showcase_rate": None,
            "observing": 0,
            "data_incomplete": 0,
            "not_matched": 0,
            "commerce_data_state": "not_applicable",
            "attributed_gmv": None,
            "attributed_orders": None,
        }

    def task_stats(
        self,
        task_id: str,
        *,
        connection=None,
    ) -> dict:
        """返回任务级商品卡归因统计，不读取组件或 attempt 明细。"""
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            raise ValueError("task_id_required")

        def execute(con) -> dict:
            task = con.execute(
                select(
                    send_task.c.task_id,
                    send_task.c.send_mode,
                ).where(send_task.c.task_id == clean_task_id)
            ).mappings().one_or_none()
            if task is None:
                raise LookupError("send_task_not_found")
            if task["send_mode"] == "text_only":
                return self._empty_task_stats(
                    state="not_applicable",
                )

            recipient_creator = case(
                (
                    send_task_attribution.c.status.in_(
                        _RECIPIENT_STATUSES
                    ),
                    send_task_attribution.c.creator_identity_key,
                ),
                else_=None,
            )
            matched_creator = case(
                (
                    send_task_attribution.c.status == "matched",
                    send_task_attribution.c.creator_identity_key,
                ),
                else_=None,
            )
            matched_pid = case(
                (
                    send_task_attribution.c.status == "matched",
                    send_task_attribution.c.pid,
                ),
                else_=None,
            )
            aggregate = con.execute(
                select(
                    func.count(func.distinct(recipient_creator)).label(
                        "card_recipient_creators"
                    ),
                    func.count(func.distinct(matched_creator)).label(
                        "matched_creators"
                    ),
                    func.count().filter(
                        send_task_attribution.c.status == "matched"
                    ).label("matched_pairs"),
                    func.count(func.distinct(matched_pid)).label(
                        "distinct_pids"
                    ),
                    func.count().filter(
                        send_task_attribution.c.status == "observing"
                    ).label("observing"),
                    func.count().filter(
                        send_task_attribution.c.status
                        == "data_incomplete"
                    ).label("data_incomplete"),
                    func.count().filter(
                        send_task_attribution.c.status == "not_matched"
                    ).label("not_matched"),
                )
                .where(
                    send_task_attribution.c.task_id == clean_task_id
                )
            ).mappings().one()
            commerce_row = con.execute(
                self._commerce_task_statement(task_id=clean_task_id)
            ).mappings().one()
            counts = {
                key: int(aggregate.get(key) or 0)
                for key in (
                    "card_recipient_creators",
                    "matched_creators",
                    "matched_pairs",
                    "distinct_pids",
                    "observing",
                    "data_incomplete",
                    "not_matched",
                )
            }
            denominator = counts["card_recipient_creators"]
            rate = (
                round(counts["matched_creators"] / denominator, 4)
                if denominator
                else None
            )
            if counts["data_incomplete"]:
                state = "data_incomplete"
            elif counts["observing"]:
                state = "observing"
            elif denominator:
                state = "complete"
            else:
                state = "pending"
            commerce = self._commerce_decision(commerce_row)
            return {
                "state": state,
                **counts,
                "showcase_rate": rate,
                "commerce_data_state": commerce.state,
                "attributed_gmv": commerce.attributed_gmv,
                "attributed_orders": commerce.attributed_orders,
            }

        if connection is not None:
            return execute(connection)
        with self.engine.connect() as own_connection:
            return execute(own_connection)

    def list_task_creator_results(
        self,
        task_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        connection=None,
    ) -> dict:
        """按达人聚合任务归因结果，并在数据库层分页。"""
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            raise ValueError("task_id_required")
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("invalid_limit")
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("invalid_offset")

        commerce = self._commerce_per_creator()
        signals = self._creator_task_signals()
        joined = (
            send_task_attribution.join(
                send_task_lead,
                send_task_lead.c.lead_id
                == send_task_attribution.c.lead_id,
            ).outerjoin(
                commerce,
                and_(
                    commerce.c.task_id
                    == send_task_attribution.c.task_id,
                    commerce.c.creator_identity_key
                    == send_task_attribution.c.creator_identity_key,
                ),
            ).outerjoin(
                signals,
                and_(
                    signals.c.task_id == send_task_attribution.c.task_id,
                    signals.c.creator_identity_key
                    == send_task_attribution.c.creator_identity_key,
                ),
            )
        )
        recipient_filter = and_(
            send_task_attribution.c.task_id == clean_task_id,
            send_task_attribution.c.status.in_(_RECIPIENT_STATUSES),
        )
        matched_pid = case(
            (
                send_task_attribution.c.status == "matched",
                send_task_attribution.c.pid,
            ),
            else_=None,
        )
        matched_report_date = case(
            (
                send_task_attribution.c.status == "matched",
                send_task_attribution.c.matched_report_date,
            ),
            else_=None,
        )

        def execute(con) -> dict:
            task = con.execute(
                select(
                    send_task.c.task_id,
                    send_task.c.send_mode,
                ).where(send_task.c.task_id == clean_task_id)
            ).mappings().one_or_none()
            if task is None:
                raise LookupError("send_task_not_found")
            if task["send_mode"] == "text_only":
                return {
                    "rows": [],
                    "pagination": {
                        "total": 0,
                        "limit": limit,
                        "offset": offset,
                        "next_offset": None,
                    },
                }

            total = int(
                con.execute(
                    select(
                        func.count(
                            func.distinct(
                                send_task_attribution.c.creator_identity_key
                            )
                        )
                    )
                    .select_from(joined)
                    .where(recipient_filter)
                ).scalar_one()
                or 0
            )
            grouped = con.execute(
                select(
                    send_task_attribution.c.creator_identity_key,
                    func.min(send_task_lead.c.raw_handle).label(
                        "raw_handle"
                    ),
                    func.count(
                        func.distinct(send_task_attribution.c.pid)
                    ).label("card_pid_count"),
                    func.count(func.distinct(matched_pid)).label(
                        "matched_pid_count"
                    ),
                    func.min(matched_report_date).label(
                        "first_matched_report_date"
                    ),
                    func.count().filter(
                        send_task_attribution.c.status == "matched"
                    ).label("matched_count"),
                    func.count().filter(
                        send_task_attribution.c.status
                        == "data_incomplete"
                    ).label("data_incomplete_count"),
                    func.count().filter(
                        send_task_attribution.c.status == "observing"
                    ).label("observing_count"),
                    func.count().filter(
                        send_task_attribution.c.status == "not_matched"
                    ).label("not_matched_count"),
                    func.max(commerce.c.commerce_matched_pairs).label(
                        "commerce_matched_pairs"
                    ),
                    func.max(commerce.c.gmv_evidence_pairs).label(
                        "gmv_evidence_pairs"
                    ),
                    func.max(commerce.c.order_evidence_pairs).label(
                        "order_evidence_pairs"
                    ),
                    func.max(commerce.c.attributed_gmv).label(
                        "attributed_gmv"
                    ),
                    func.max(commerce.c.attributed_orders).label(
                        "attributed_orders"
                    ),
                    case(
                        (
                            func.count().filter(
                                signals.c.associated_reply.is_(True)
                            )
                            > 0,
                            True,
                        ),
                        else_=False,
                    ).label("associated_reply"),
                    func.max(signals.c.associated_reply_at_ms).label(
                        "associated_reply_at_ms"
                    ),
                    func.max(signals.c.reply_work_state).label(
                        "reply_work_state"
                    ),
                    case(
                        (
                            func.count().filter(
                                signals.c.manual_excluded.is_(True)
                            )
                            > 0,
                            True,
                        ),
                        else_=False,
                    ).label("manual_excluded"),
                    func.min(send_task_lead.c.source_row_no).label(
                        "first_source_row_no"
                    ),
                )
                .select_from(joined)
                .where(recipient_filter)
                .group_by(
                    send_task_attribution.c.creator_identity_key
                )
                .order_by(
                    func.min(send_task_lead.c.source_row_no),
                    send_task_attribution.c.creator_identity_key,
                )
                .limit(limit)
                .offset(offset)
            ).mappings().all()
            rows = []
            for row in grouped:
                if int(row.get("matched_count") or 0):
                    status = "matched"
                elif int(row.get("data_incomplete_count") or 0):
                    status = "data_incomplete"
                elif int(row.get("observing_count") or 0):
                    status = "observing"
                else:
                    status = "not_matched"
                commerce_result = self._commerce_decision(row)
                rows.append({
                    "creator_identity_key": row[
                        "creator_identity_key"
                    ],
                    "raw_handle": row["raw_handle"],
                    "card_pid_count": int(
                        row.get("card_pid_count") or 0
                    ),
                    "matched_pid_count": int(
                        row.get("matched_pid_count") or 0
                    ),
                    "status": status,
                    "first_matched_report_date": row.get(
                        "first_matched_report_date"
                    ),
                    "added_to_showcase": (
                        True
                        if status == "matched"
                        else False
                        if status == "not_matched"
                        else None
                    ),
                    "commerce_data_state": commerce_result.state,
                    "attributed_gmv": commerce_result.attributed_gmv,
                    "attributed_orders": commerce_result.attributed_orders,
                    "associated_reply": bool(
                        row.get("associated_reply")
                    ),
                    "associated_reply_at_ms": row.get(
                        "associated_reply_at_ms"
                    ),
                    "reply_work_state": row.get("reply_work_state"),
                    "manual_excluded": bool(row.get("manual_excluded")),
                })
            consumed = offset + len(rows)
            return {
                "rows": rows,
                "pagination": {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "next_offset": (
                        offset + limit if consumed < total else None
                    ),
                },
            }

        if connection is not None:
            return execute(connection)
        with self.engine.connect() as own_connection:
            return execute(own_connection)


__all__ = ["ActiveAttributionConflict", "SendAttributionRepo"]
