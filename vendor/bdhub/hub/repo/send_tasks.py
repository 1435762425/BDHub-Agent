"""发送任务、逻辑组件与组件发送尝试的持久化事务。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import (
    and_,
    bindparam,
    case,
    delete,
    exists,
    func,
    insert,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bdhub.hub.market_catalog import MARKET_CATALOG, get as get_dashboard_market
from bdhub.hub.schema import (
    im_conversation,
    send_component_attempt,
    send_task,
    send_task_account,
    send_task_attribution,
    send_task_component,
    send_task_event,
    send_task_lead,
)
from bdhub.send.template_rendering import (
    TemplateValidationError,
    select_import_values,
    unresolved_placeholder_names,
)


class PreflightStale(RuntimeError):
    """任务内容或名单已经变化，发送前预检结果不能再使用。"""


class TaskStateConflict(RuntimeError):
    """任务版本或组件状态不满足当前操作的前置条件。"""


_SEND_MODE_COMPONENTS = {
    "text_only": (("text", 1),),
    "card_only": (("card", 1),),
    "text_then_card": (("text", 1), ("card", 2)),
    "card_then_text": (("card", 1), ("text", 2)),
}
_TEXT_MODES = {"text_only", "text_then_card", "card_then_text"}
_CARD_MODES = {"card_only", "text_then_card", "card_then_text"}
# 显式多VALUES语句不受SQLAlchemy executemany自动分页保护；保持远低于65535参数。
_BULK_WRITE_BATCH_SIZE = 500
_NON_RETRYABLE_COMPONENT_STATES = {
    "sending",
    "unknown",
    "failed_terminal",
    "limited",
    "blocked_by_previous",
}
_ATTEMPT_STATUSES = {
    "sent",
    "failed_retryable",
    "failed_terminal",
    "limited",
    "unknown",
}

_DELETABLE_TASK_STATUSES = {
    "draft",
    "ready",
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
}


def build_task_source_summary(rows: list[dict], *, card_limit: int) -> dict:
    """按导入 Handle 汇总完整名单；不使用分页预览，也不按身份键误合并。"""
    groups: dict[str, dict] = {}
    status_rank = {"pending": 0, "eligible": 1, "warning": 2,
                   "leftover": 3, "hard_excluded": 4}
    for row in rows:
        display = str(row.get("raw_handle") or "").strip().lstrip("@")
        key = display.lower()
        group = groups.setdefault(key, {
            "handle": display,
            "creator_identity_key": row.get("creator_identity_key"),
            "pids": set(),
            "match_status": row.get("match_status") or "unresolved",
            "eligibility_status": row.get("eligibility_status") or "pending",
            "gmv_tier": "unknown",
            "gmv_mxn": None,
        })
        pid = str(row.get("pid") or "").strip()
        if pid:
            group["pids"].add(pid)
        if row.get("creator_identity_key"):
            group["creator_identity_key"] = row["creator_identity_key"]
        if row.get("match_status") and row.get("match_status") != "unresolved":
            group["match_status"] = row["match_status"]
        current = str(group["eligibility_status"])
        candidate = str(row.get("eligibility_status") or "pending")
        if status_rank.get(candidate, 0) > status_rank.get(current, 0):
            group["eligibility_status"] = candidate
        snapshot = row.get("gmv_tier_snapshot")
        if isinstance(snapshot, dict) and snapshot.get("tier"):
            group["gmv_tier"] = str(snapshot["tier"])
            group["gmv_mxn"] = snapshot.get("local_value")

    creators = []
    pid_counts: dict[int, dict] = {}
    gmv_counts: dict[str, int] = {}
    for group in groups.values():
        pid_count = len(group.pop("pids"))
        creators.append({**group, "pid_count": pid_count})
        bucket = pid_counts.setdefault(pid_count, {
            "pid_count": pid_count, "creator_count": 0,
            "leftover_pid_count": 0,
        })
        bucket["creator_count"] += 1
        bucket["leftover_pid_count"] += max(0, pid_count - int(card_limit))
        tier = str(group["gmv_tier"])
        gmv_counts[tier] = gmv_counts.get(tier, 0) + 1
    creators.sort(key=lambda item: item["handle"].lower())
    matched = sum(bool(item["creator_identity_key"]) for item in creators)
    return {
        "input_rows": len(rows),
        "unique_handles": len(creators),
        "matched_handles": matched,
        "unresolved_handles": len(creators) - matched,
        "pid_relations": sum(item["pid_count"] for item in creators),
        "multi_pid_handles": sum(item["pid_count"] > 1 for item in creators),
        "pid_distribution": [pid_counts[key] for key in sorted(pid_counts)],
        "gmv_distribution": [
            {"tier": tier, "creator_count": count}
            for tier, count in sorted(gmv_counts.items(), key=lambda item: (item[0] == "unknown", item[0]))
        ],
        "creators": creators,
    }


def build_leftover_follow_up_task_values(
    source_task: dict,
    leftovers: list[dict],
    *,
    follow_up_task_id: str,
    actor: str,
    now: datetime,
    name: str | None = None,
) -> dict:
    """构造遗留任务的冻结配置。

    默认 PID 必须来自本次遗留行，不能沿用原任务已处理的 PID。
    """
    default_pid = next(
        (
            clean_pid
            for leftover in leftovers
            if (clean_pid := str(leftover.get("pid") or "").strip())
        ),
        None,
    )
    clean_name = str(name or "").strip()
    return {
        "task_id": follow_up_task_id,
        "bd_market": source_task["bd_market"],
        "name": clean_name or f"{source_task['name']} · 遗留任务",
        "source_type": source_task["source_type"],
        "send_mode": "card_only",
        "template_id": None,
        "text_snapshot": None,
        "template_snapshot": None,
        "default_pid": default_pid,
        "card_limit_per_creator": source_task.get(
            "card_limit_per_creator", 2
        ),
        "status": "draft",
        "status_reason": None,
        "revision": 1,
        "preflight_revision": None,
        "created_by": actor,
        "updated_by": actor,
        "created_at": now,
        "updated_at": now,
    }


class SendTaskRepo:
    def __init__(
        self,
        engine,
        *,
        now=None,
        id_factory=None,
        attempt_stale_after: timedelta | None = None,
    ):
        self.engine = engine
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.id_factory = id_factory or (
            lambda kind: f"{kind}_{uuid4().hex}"
        )
        self.attempt_stale_after = (
            attempt_stale_after or timedelta(minutes=15)
        )

    def _locked_task(self, connection, task_id: str) -> dict:
        row = connection.execute(
            select(send_task)
            .where(send_task.c.task_id == task_id)
            .with_for_update()
        ).mappings().one_or_none()
        if row is None:
            raise LookupError("send_task_not_found")
        return dict(row)

    @staticmethod
    def _require_revision(task: dict, expected_revision: int) -> None:
        if int(task["revision"]) != int(expected_revision):
            raise TaskStateConflict("revision_conflict")

    @staticmethod
    def _task_dict(row) -> dict:
        """把 Text 列里的结构化预检摘要安全还原，普通原因保持文本。"""
        task = dict(row)
        reason = task.get("status_reason")
        if not isinstance(reason, str):
            return task
        try:
            decoded = json.loads(reason)
        except (TypeError, ValueError):
            return task
        if isinstance(decoded, dict):
            task["status_reason"] = decoded
        return task

    def _append_event(
        self,
        connection,
        *,
        task_id: str,
        event_type: str,
        actor: str,
        lead_id: str | None = None,
        payload: dict | None = None,
    ) -> str:
        event_id = self.id_factory("event")
        connection.execute(
            insert(send_task_event).values(
                event_id=event_id,
                task_id=task_id,
                lead_id=lead_id,
                event_type=event_type,
                actor=actor,
                payload=payload or {},
                created_at=self.now(),
            )
        )
        return event_id

    @staticmethod
    def _clean_actor(actor: object) -> str:
        clean_actor = str(actor or "").strip()
        if not clean_actor:
            raise ValueError("actor_required")
        return clean_actor

    def create_task(
        self,
        *,
        market,
        name,
        source_type,
        actor,
        send_mode="text_only",
    ) -> dict:
        clean_market = str(market or "").strip().lower()
        clean_name = str(name or "").strip()
        clean_actor = self._clean_actor(actor)
        if clean_market not in MARKET_CATALOG:
            raise ValueError("unsupported_market")
        if not clean_name:
            raise ValueError("task_name_required")
        if send_mode not in {"text_only", "card_then_text"}:
            raise ValueError("invalid_creation_send_mode")

        now = self.now()
        task_id = self.id_factory("task")
        values = {
            "task_id": task_id,
            "bd_market": clean_market,
            "name": clean_name,
            "source_type": str(source_type or "manual"),
            "send_mode": send_mode,
            "status": "draft",
            "revision": 1,
            "created_by": clean_actor,
            "updated_by": clean_actor,
            "created_at": now,
            "updated_at": now,
        }
        with self.engine.begin() as connection:
            row = connection.execute(
                insert(send_task).values(**values).returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                event_type="task_created",
                actor=clean_actor,
                payload={"source_type": values["source_type"]},
            )
            return self._task_dict(row)

    def rename_task(self, task_id: str, *, name: str, actor: str) -> dict:
        clean_name = str(name or "").strip()
        clean_actor = self._clean_actor(actor)
        if not clean_name:
            raise ValueError("task_name_required")
        if len(clean_name) > 120:
            raise ValueError("task_name_too_long")
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            updated = connection.execute(
                update(send_task).where(send_task.c.task_id == task_id).values(
                    name=clean_name,
                    updated_by=clean_actor,
                    updated_at=self.now(),
                ).returning(send_task)
            ).mappings().one()
            self._append_event(
                connection, task_id=task_id, event_type="task_renamed",
                actor=clean_actor,
                payload={"old_name": task.get("name"), "new_name": clean_name},
            )
            return self._task_dict(updated)

    def delete_task(self, task_id: str, *, actor: str) -> dict:
        self._clean_actor(actor)
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            if task.get("status") not in _DELETABLE_TASK_STATUSES:
                raise TaskStateConflict("task_delete_not_allowed")

            lead_ids = select(send_task_lead.c.lead_id).where(
                send_task_lead.c.task_id == task_id,
            )
            component_ids = select(send_task_component.c.component_id).where(
                send_task_component.c.lead_id.in_(lead_ids),
            )
            connection.execute(delete(send_component_attempt).where(
                send_component_attempt.c.component_id.in_(component_ids)
            ))
            connection.execute(delete(send_task_attribution).where(
                send_task_attribution.c.task_id == task_id
            ))
            connection.execute(delete(send_task_event).where(
                send_task_event.c.task_id == task_id
            ))
            connection.execute(delete(send_task_component).where(
                send_task_component.c.lead_id.in_(lead_ids)
            ))
            connection.execute(delete(send_task_lead).where(
                send_task_lead.c.task_id == task_id
            ))
            deleted = connection.execute(delete(send_task).where(
                send_task.c.task_id == task_id
            ))
            return {"task_id": task_id, "deleted": int(deleted.rowcount or 0)}

    def create_leftover_follow_up(
        self,
        task_id: str,
        *,
        actor: str,
        expected_revision: int,
        name: str | None = None,
    ) -> dict:
        """把商品卡上限产生的遗留行复制成一份重新预检的待执行任务。"""
        clean_actor = self._clean_actor(actor)
        now = self.now()
        with self.engine.begin() as connection:
            source_task = self._locked_task(connection, task_id)
            self._require_revision(source_task, expected_revision)
            existing_event = connection.execute(
                select(send_task_event.c.payload)
                .where(
                    send_task_event.c.task_id == task_id,
                    send_task_event.c.event_type
                    == "leftover_follow_up_created",
                )
                .order_by(send_task_event.c.created_at.desc())
                .limit(1)
            ).mappings().one_or_none()
            existing_payload = (
                existing_event.get("payload")
                if existing_event is not None
                else None
            )
            existing_task_id = (
                str(existing_payload.get("follow_up_task_id") or "").strip()
                if isinstance(existing_payload, dict)
                else ""
            )
            existing_source_revision = (
                existing_payload.get("source_revision")
                if isinstance(existing_payload, dict)
                else None
            )
            if (
                existing_task_id
                and existing_source_revision == source_task.get("revision")
            ):
                existing_task = connection.execute(
                    select(send_task).where(
                        send_task.c.task_id == existing_task_id
                    )
                ).mappings().one_or_none()
                if existing_task is None:
                    raise LookupError("leftover_follow_up_not_found")
                return {
                    "task": self._task_dict(existing_task),
                    "lead_count": int(
                        existing_payload.get("lead_count") or 0
                    ),
                }
            if (
                source_task.get("status") != "ready"
                or source_task.get("preflight_revision")
                != source_task.get("revision")
            ):
                raise TaskStateConflict("leftover_source_not_ready")
            leftovers = [
                dict(row)
                for row in connection.execute(
                    select(send_task_lead)
                    .where(
                        send_task_lead.c.task_id == task_id,
                        or_(
                            send_task_lead.c.eligibility_status == "leftover",
                            send_task_lead.c.exclusion_code
                            == "card_cap_leftover",
                        ),
                        send_task_lead.c.manual_excluded.is_(False),
                    )
                    .order_by(send_task_lead.c.source_row_no.asc())
                ).mappings().all()
            ]
            if not leftovers:
                raise ValueError("no_leftover_leads")

            follow_up_task_id = self.id_factory("task")
            task_values = build_leftover_follow_up_task_values(
                source_task,
                leftovers,
                follow_up_task_id=follow_up_task_id,
                actor=clean_actor,
                now=now,
                name=name,
            )
            created_task = connection.execute(
                insert(send_task)
                .values(**task_values)
                .returning(send_task)
            ).mappings().one()

            lead_values = []
            for source_row_no, source in enumerate(leftovers, start=1):
                old_snapshot = source.get("source_snapshot")
                import_values = (
                    old_snapshot.get("import_values")
                    if isinstance(old_snapshot, dict)
                    else None
                )
                source_snapshot = {
                    "leftover_origin": {
                        "task_id": task_id,
                        "lead_id": source["lead_id"],
                        "source_row_no": source["source_row_no"],
                    }
                }
                if isinstance(import_values, dict) and import_values:
                    source_snapshot["import_values"] = dict(import_values)
                lead_values.append(
                    {
                        "lead_id": self.id_factory("lead"),
                        "task_id": follow_up_task_id,
                        "source_row_no": source_row_no,
                        "raw_handle": source["raw_handle"],
                        "creator_identity_key": source.get(
                            "creator_identity_key"
                        ),
                        "oec_id": source.get("oec_id"),
                        "pid": source.get("pid"),
                        "input_fingerprint": (
                            f"leftover:{task_id}:{source['lead_id']}"
                        ),
                        "rendered_text_snapshot": None,
                        "match_status": source.get(
                            "match_status", "unresolved"
                        ),
                        "eligibility_status": "pending",
                        "exclusion_code": None,
                        "manual_excluded": False,
                        "source_snapshot": source_snapshot,
                        "gmv_tier_snapshot": {},
                        "relationship_snapshot": {},
                        "status": "pending",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            connection.execute(insert(send_task_lead).values(lead_values))
            self._append_event(
                connection,
                task_id=follow_up_task_id,
                event_type="task_created",
                actor=clean_actor,
                payload={
                    "source_type": source_task["source_type"],
                    "source_task_id": task_id,
                },
            )
            self._append_event(
                connection,
                task_id=task_id,
                event_type="leftover_follow_up_created",
                actor=clean_actor,
                payload={
                    "follow_up_task_id": follow_up_task_id,
                    "lead_count": len(lead_values),
                    "source_revision": int(source_task["revision"]),
                },
            )
            return {
                "task": self._task_dict(created_task),
                "lead_count": len(lead_values),
            }

    def get_task(self, task_id: str, *, connection=None) -> dict | None:
        def execute(con):
            row = con.execute(
                select(send_task).where(send_task.c.task_id == task_id)
            ).mappings().one_or_none()
            return self._task_dict(row) if row is not None else None

        if connection is not None:
            return execute(connection)
        with self.engine.connect() as own_connection:
            return execute(own_connection)

    def list_task_leads(
        self,
        task_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        clean_limit = int(limit)
        clean_offset = int(offset)
        if not 1 <= clean_limit <= 1_000:
            raise ValueError("invalid_lead_limit")
        if clean_offset < 0:
            raise ValueError("invalid_lead_offset")
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(send_task_lead)
                    .where(send_task_lead.c.task_id == task_id)
                    .order_by(send_task_lead.c.source_row_no.asc())
                    .limit(clean_limit)
                    .offset(clean_offset)
                ).mappings().all()
            ]

    def count_task_leads(self, task_id: str) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(
                select(func.count())
                .select_from(send_task_lead)
                .where(send_task_lead.c.task_id == task_id)
            ).scalar_one())

    def task_source_summary(self, task_id: str) -> dict:
        with self.engine.connect() as connection:
            task = self.get_task(task_id, connection=connection)
            if task is None:
                raise LookupError("send_task_not_found")
            rows = [dict(row) for row in connection.execute(
                select(
                    send_task_lead.c.raw_handle,
                    send_task_lead.c.creator_identity_key,
                    send_task_lead.c.pid,
                    send_task_lead.c.match_status,
                    send_task_lead.c.eligibility_status,
                    send_task_lead.c.gmv_tier_snapshot,
                ).where(send_task_lead.c.task_id == task_id)
            ).mappings().all()]
            return build_task_source_summary(
                rows,
                card_limit=int(task.get("card_limit_per_creator") or 2),
            )

    def execution_stats(
        self,
        task_id: str,
        *,
        window_minutes: int = 10,
    ) -> dict:
        """返回账号分布、最近速度和 ETA，不下发任何发送动作。"""
        clean_task_id = str(task_id or "").strip()
        clean_window = int(window_minutes)
        if not clean_task_id:
            raise ValueError("task_id_required")
        if not 1 <= clean_window <= 60:
            raise ValueError("invalid_stats_window")

        now = self.now()
        cutoff = now - timedelta(minutes=clean_window)
        component_join = send_task_component.join(
            send_task_lead,
            send_task_component.c.lead_id == send_task_lead.c.lead_id,
        )
        attempt_join = send_component_attempt.join(
            send_task_component,
            send_component_attempt.c.component_id
            == send_task_component.c.component_id,
        ).join(
            send_task_lead,
            send_task_component.c.lead_id == send_task_lead.c.lead_id,
        )
        resolution = send_component_attempt.c.transport_snapshot["manual_resolution"]["resolution"].astext
        effective_attempt_status = case(
            (and_(send_task_component.c.status=='pending',send_component_attempt.c.transport_snapshot['not_dispatched_requeued'].astext=='true'),'pending'),
            (and_(send_task_component.c.status == "sent", resolution == "confirmed_sent"), "sent"),
            (and_(send_task_component.c.status == "failed_retryable", resolution == "confirmed_not_sent"), "failed_retryable"),
            else_=send_component_attempt.c.status,
        )
        latest_attempt = (
            select(
                send_component_attempt.c.send_account,
                effective_attempt_status.label("status"),
                func.row_number().over(
                    partition_by=send_component_attempt.c.component_id,
                    order_by=send_component_attempt.c.attempt_no.desc(),
                ).label("attempt_rank"),
            )
            .select_from(attempt_join)
            .where(send_task_lead.c.task_id == clean_task_id)
            .subquery("latest_send_attempt")
        )
        latest_unknown_attempt = (
            select(
                send_component_attempt.c.component_id,
                send_component_attempt.c.send_account,
                send_component_attempt.c.worker_id,
                send_component_attempt.c.started_at,
                send_component_attempt.c.finished_at,
                send_component_attempt.c.error_code,
                send_component_attempt.c.error_detail,
                func.row_number().over(
                    partition_by=send_component_attempt.c.component_id,
                    order_by=send_component_attempt.c.attempt_no.desc(),
                ).label("attempt_rank"),
            )
            .select_from(attempt_join)
            .where(
                send_task_lead.c.task_id == clean_task_id,
                send_component_attempt.c.status == "unknown",
            )
            .subquery("latest_unknown_attempt")
        )

        with self.engine.connect() as connection:
            task = connection.execute(
                select(
                    send_task.c.task_id,
                    send_task.c.bd_market,
                    send_task.c.status,
                ).where(
                    send_task.c.task_id == clean_task_id
                )
            ).mappings().one_or_none()
            if task is None:
                raise LookupError("send_task_not_found")

            total_rows = connection.execute(
                select(
                    send_task_component.c.status,
                    func.count().label("count"),
                )
                .select_from(component_join)
                .where(send_task_lead.c.task_id == clean_task_id)
                .group_by(send_task_component.c.status)
            ).mappings().all()
            account_rows = connection.execute(
                select(
                    latest_attempt.c.send_account,
                    latest_attempt.c.status,
                    func.count().label("count"),
                )
                .where(
                    latest_attempt.c.attempt_rank == 1,
                    latest_attempt.c.send_account.is_not(None),
                )
                .group_by(
                    latest_attempt.c.send_account,
                    latest_attempt.c.status,
                )
                .order_by(latest_attempt.c.send_account)
            ).mappings().all()
            recent = connection.execute(
                select(
                    func.count().label("sent_count"),
                    func.min(
                        send_component_attempt.c.finished_at
                    ).label("first_sent_at"),
                )
                .select_from(attempt_join)
                .where(
                    send_task_lead.c.task_id == clean_task_id,
                    effective_attempt_status == "sent",
                    send_component_attempt.c.finished_at >= cutoff,
                )
            ).mappings().one()
            unknown_rows = connection.execute(
                select(
                    send_task_component.c.component_id,
                    send_task_component.c.lead_id,
                    send_task_lead.c.raw_handle,
                    send_task_component.c.component_kind,
                    send_task_component.c.sequence_no,
                    send_task_component.c.attempt_count,
                    send_task_component.c.updated_at,
                    latest_unknown_attempt.c.send_account,
                    latest_unknown_attempt.c.worker_id,
                    latest_unknown_attempt.c.started_at,
                    latest_unknown_attempt.c.finished_at,
                    latest_unknown_attempt.c.error_code,
                    latest_unknown_attempt.c.error_detail,
                )
                .select_from(
                    component_join.outerjoin(
                        latest_unknown_attempt,
                        and_(
                            latest_unknown_attempt.c.component_id
                            == send_task_component.c.component_id,
                            latest_unknown_attempt.c.attempt_rank == 1,
                        ),
                    )
                )
                .where(
                    send_task_lead.c.task_id == clean_task_id,
                    send_task_component.c.status == "unknown",
                )
                .order_by(
                    send_task_component.c.updated_at.asc(),
                    send_task_component.c.component_id.asc(),
                )
                .limit(100)
            ).mappings().all()

        status_counts = {
            str(row["status"]): int(row["count"])
            for row in total_rows
        }
        failed_states = {
            "failed_retryable",
            "failed_terminal",
            "limited",
        }
        remaining_states = {
            "pending",
            "failed_retryable",
            "sending",
            "blocked_by_previous",
        }
        totals = {
            "total": sum(status_counts.values()),
            "sent": status_counts.get("sent", 0),
            "failed": sum(
                status_counts.get(status, 0)
                for status in failed_states
            ),
            "retryable": status_counts.get("failed_retryable", 0),
            "unknown": status_counts.get("unknown", 0),
            "sending": status_counts.get("sending", 0),
            "remaining": sum(
                status_counts.get(status, 0)
                for status in remaining_states
            ),
        }

        by_account: dict[str, dict] = {}
        for row in account_rows:
            account_name = str(row["send_account"])
            stats = by_account.setdefault(
                account_name,
                {
                    "account_name": account_name,
                    "sent": 0,
                    "failed": 0,
                    "unknown": 0,
                    "sending": 0,
                    "total": 0,
                },
            )
            status = str(row["status"])
            count = int(row["count"])
            stats["total"] += count
            if status == "sent":
                stats["sent"] += count
            elif status == "unknown":
                stats["unknown"] += count
            elif status == "sending":
                stats["sending"] += count
            elif status in failed_states:
                stats["failed"] += count

        sent_count = int(recent.get("sent_count") or 0)
        first_sent_at = recent.get("first_sent_at")
        speed_per_minute = 0.0
        if sent_count > 0 and isinstance(first_sent_at, datetime):
            if first_sent_at.tzinfo is None and now.tzinfo is not None:
                first_sent_at = first_sent_at.replace(tzinfo=now.tzinfo)
            elapsed_seconds = min(
                clean_window * 60,
                max(60.0, (now - first_sent_at).total_seconds()),
            )
            speed_per_minute = round(
                sent_count / (elapsed_seconds / 60),
                2,
            )
        eta_seconds = None
        if (
            str(task["status"]) == "running"
            and totals["remaining"] > 0
            and speed_per_minute > 0
        ):
            eta_seconds = int(round(
                totals["remaining"] / speed_per_minute * 60
            ))

        return {
            "task_id": clean_task_id,
            "bd_market": str(task["bd_market"]),
            "status": str(task["status"]),
            "totals": totals,
            "accounts": list(by_account.values()),
            "recent": {
                "window_minutes": clean_window,
                "sent": sent_count,
                "speed_per_minute": speed_per_minute,
                "eta_seconds": eta_seconds,
            },
            "unknown_components": [dict(row) for row in unknown_rows],
        }

    def list_tasks(self, *, market: str = "mx", limit: int = 100) -> list[dict]:
        clean_market = str(market or "").strip().lower()
        if clean_market not in MARKET_CATALOG:
            raise ValueError("unsupported_market")
        if not 1 <= int(limit) <= 500:
            raise ValueError("task_limit_out_of_range")
        with self.engine.connect() as connection:
            return [
                self._task_dict(row)
                for row in connection.execute(
                    select(send_task)
                    .where(send_task.c.bd_market == clean_market)
                    .order_by(send_task.c.created_at.desc())
                    .limit(int(limit))
                ).mappings()
            ]

    def set_content(
        self,
        task_id,
        *,
        expected_revision,
        send_mode,
        template_id,
        text_snapshot,
        default_pid,
        actor,
        template_snapshot=None,
        card_limit_per_creator=2,
    ) -> dict:
        if send_mode not in _SEND_MODE_COMPONENTS:
            raise ValueError("invalid_send_mode")
        clean_text = str(text_snapshot or "").strip() or None
        clean_pid = str(default_pid or "").strip() or None
        clean_actor = self._clean_actor(actor)
        if isinstance(card_limit_per_creator, bool):
            raise ValueError("card_limit_out_of_range")
        try:
            clean_card_limit = int(card_limit_per_creator)
        except (TypeError, ValueError) as exc:
            raise ValueError("card_limit_out_of_range") from exc
        if not 1 <= clean_card_limit <= 4:
            raise ValueError("card_limit_out_of_range")
        if send_mode in _TEXT_MODES and clean_text is None:
            raise ValueError("text_required")
        if send_mode in _CARD_MODES and clean_pid is None:
            raise ValueError("pid_required")

        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            self._require_revision(task, expected_revision)
            if task["status"] not in {"draft", "ready"}:
                raise TaskStateConflict("task_not_mutable")
            new_revision = int(task["revision"]) + 1
            row = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    send_mode=send_mode,
                    template_id=template_id,
                    text_snapshot=clean_text,
                    template_snapshot=template_snapshot,
                    default_pid=clean_pid,
                    card_limit_per_creator=clean_card_limit,
                    status="draft",
                    status_reason=None,
                    revision=new_revision,
                    preflight_revision=None,
                    updated_by=clean_actor,
                    updated_at=self.now(),
                )
                .returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                event_type="content_changed",
                actor=clean_actor,
                payload={"send_mode": send_mode, "revision": new_revision},
            )
            return self._task_dict(row)

    def add_leads(self, task_id, rows, *, expected_revision, actor) -> dict:
        source_rows = list(rows)
        clean_actor = self._clean_actor(actor)
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            self._require_revision(task, expected_revision)
            if task["status"] not in {"draft", "ready"}:
                raise TaskStateConflict("task_not_mutable")

            now = self.now()
            payload = []
            snapshot = task.get("template_snapshot")
            declarations = (
                snapshot.get("placeholders") or []
                if isinstance(snapshot, dict)
                else []
            )
            seen_fingerprints: set[str] = set()
            for source in source_rows:
                raw_handle = str(source["raw_handle"])
                if not raw_handle.strip():
                    raise ValueError("raw_handle_required")
                fingerprint = str(source["input_fingerprint"])
                if fingerprint in seen_fingerprints:
                    continue
                seen_fingerprints.add(fingerprint)
                import_values = select_import_values(
                    source.get("values") or {},
                    declarations,
                )
                payload.append(
                    {
                        "lead_id": self.id_factory("lead"),
                        "task_id": task_id,
                        "source_row_no": int(source["source_row_no"]),
                        "raw_handle": raw_handle,
                        "creator_identity_key": source.get("creator_identity_key"),
                        "oec_id": source.get("oec_id"),
                        "pid": source.get("pid"),
                        "input_fingerprint": fingerprint,
                        "match_status": source.get("match_status", "unresolved"),
                        "eligibility_status": "pending",
                        "manual_excluded": False,
                        "source_snapshot": (
                            {"import_values": import_values}
                            if import_values
                            else {}
                        ),
                        "gmv_tier_snapshot": {},
                        "relationship_snapshot": {},
                        "status": "pending",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            for offset in range(0, len(payload), _BULK_WRITE_BATCH_SIZE):
                statement = pg_insert(send_task_lead).values(payload[offset:offset + _BULK_WRITE_BATCH_SIZE])
                connection.execute(
                    statement.on_conflict_do_update(
                        constraint="uq_send_task_lead_input",
                        set_={
                            "source_row_no": statement.excluded.source_row_no,
                            "raw_handle": statement.excluded.raw_handle,
                            "creator_identity_key": (
                                statement.excluded.creator_identity_key
                            ),
                            "oec_id": statement.excluded.oec_id,
                            "pid": statement.excluded.pid,
                            "rendered_text_snapshot": None,
                            "match_status": statement.excluded.match_status,
                            "eligibility_status": (
                                statement.excluded.eligibility_status
                            ),
                            "exclusion_code": None,
                            "manual_excluded": statement.excluded.manual_excluded,
                            "source_snapshot": statement.excluded.source_snapshot,
                            "gmv_tier_snapshot": (
                                statement.excluded.gmv_tier_snapshot
                            ),
                            "relationship_snapshot": (
                                statement.excluded.relationship_snapshot
                            ),
                            "status": statement.excluded.status,
                            "updated_at": statement.excluded.updated_at,
                        },
                    )
                )
            new_revision = int(task["revision"]) + 1
            task_row = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status="draft",
                    status_reason=None,
                    revision=new_revision,
                    preflight_revision=None,
                    updated_by=clean_actor,
                    updated_at=now,
                )
                .returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                event_type="leads_imported",
                actor=clean_actor,
                payload={
                    "received_rows": len(source_rows),
                    "revision": new_revision,
                },
            )
            return self._task_dict(task_row)

    def set_preflight_ready(self, task_id, *, expected_revision) -> dict:
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            self._require_revision(task, expected_revision)
            row = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status="ready",
                    status_reason=None,
                    preflight_revision=expected_revision,
                    updated_at=self.now(),
                )
                .returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                event_type="preflight_ready",
                actor=task["updated_by"],
                payload={"revision": expected_revision},
            )
            return self._task_dict(row)

    def apply_preflight(
        self,
        task_id: str,
        *,
        expected_revision: int,
        updates,
        summary: dict,
        actor: str,
    ) -> dict:
        """原子写入同一版本的预检快照，并把任务推进为可执行状态。"""
        clean_actor = self._clean_actor(actor)
        rows = [dict(item) for item in updates]
        for row in rows:
            row.setdefault("rendered_text_snapshot", None)
        lead_ids = [str(row.get("lead_id") or "").strip() for row in rows]
        if any(not lead_id for lead_id in lead_ids):
            raise ValueError("preflight_lead_id_required")
        if len(set(lead_ids)) != len(lead_ids):
            raise ValueError("duplicate_preflight_lead")

        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            self._require_revision(task, expected_revision)
            if task["status"] not in {"draft", "ready", "preflighting"}:
                raise TaskStateConflict("task_not_preflightable")

            known_count = connection.execute(
                select(send_task_lead.c.lead_id).where(
                    send_task_lead.c.task_id == task_id,
                    send_task_lead.c.lead_id.in_(lead_ids),
                )
            ).scalars().all() if lead_ids else []
            if len(known_count) != len(lead_ids):
                raise LookupError("preflight_lead_not_in_task")

            if rows:
                statement = (
                    update(send_task_lead)
                    .where(
                        send_task_lead.c.task_id == task_id,
                        send_task_lead.c.lead_id == bindparam("_lead_id"),
                    )
                    .values(
                        creator_identity_key=bindparam("creator_identity_key"),
                        oec_id=bindparam("oec_id"),
                        pid=bindparam("pid"),
                        rendered_text_snapshot=bindparam(
                            "rendered_text_snapshot"
                        ),
                        match_status=bindparam("match_status"),
                        eligibility_status=bindparam("eligibility_status"),
                        exclusion_code=bindparam("exclusion_code"),
                        source_snapshot=bindparam("source_snapshot"),
                        gmv_tier_snapshot=bindparam("gmv_tier_snapshot"),
                        relationship_snapshot=bindparam("relationship_snapshot"),
                        status=bindparam("status"),
                        updated_at=self.now(),
                    )
                )
                connection.execute(statement, [
                    {**row, "_lead_id": row["lead_id"]}
                    for row in rows
                ])

            ready = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status="ready",
                    status_reason=json.dumps(
                        dict(summary),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    preflight_revision=expected_revision,
                    updated_by=clean_actor,
                    updated_at=self.now(),
                )
                .returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                event_type="preflight_completed",
                actor=clean_actor,
                payload={"revision": expected_revision, "summary": dict(summary)},
            )
            return self._task_dict(ready)

    def set_manual_exclusion(
        self,
        task_id,
        lead_ids,
        *,
        excluded,
        reason,
        actor,
        expected_revision,
    ) -> int:
        clean_ids = sorted({str(value) for value in lead_ids if value})
        if not clean_ids:
            return 0
        clean_actor = self._clean_actor(actor)
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            self._require_revision(task, expected_revision)
            result = connection.execute(
                update(send_task_lead)
                .where(
                    send_task_lead.c.task_id == task_id,
                    send_task_lead.c.lead_id.in_(clean_ids),
                )
                .values(
                    manual_excluded=bool(excluded),
                    exclusion_code=(
                        str(reason or "manual_excluded") if excluded else None
                    ),
                    updated_at=self.now(),
                )
            )
            changed = int(result.rowcount or 0)
            if changed != len(clean_ids):
                raise LookupError("send_task_lead_not_found")
            new_revision = int(task["revision"]) + 1
            connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status="draft",
                    status_reason=None,
                    revision=new_revision,
                    preflight_revision=None,
                    updated_by=clean_actor,
                    updated_at=self.now(),
                )
            )
            self._append_event(
                connection,
                task_id=task_id,
                event_type=(
                    "leads_manually_excluded"
                    if excluded
                    else "leads_manual_exclusion_removed"
                ),
                actor=clean_actor,
                payload={
                    "lead_ids": clean_ids,
                    "reason": reason,
                    "revision": new_revision,
                },
            )
            return changed

    def claim_ready_task(self, task_id, *, expected_revision) -> dict:
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            self._require_revision(task, expected_revision)
            if task["preflight_revision"] != task["revision"]:
                raise PreflightStale(task_id)
            if task["status"] != "ready":
                raise TaskStateConflict("task_not_ready")
            now = self.now()
            row = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status="running",
                    started_at=task["started_at"] or now,
                    updated_at=now,
                )
                .returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                event_type="http_task_confirmed" if hold_for_http else "task_started",
                actor=task["updated_by"],
                payload={"revision": task["revision"]},
            )
            return self._task_dict(row)

    @staticmethod
    def _allowed_components(task: dict, lead: dict) -> set[str]:
        selected = {
            kind
            for kind, _sequence_no
            in _SEND_MODE_COMPONENTS[task["send_mode"]]
        }
        snapshot = lead["source_snapshot"] or {}
        if "allowed_components" in snapshot:
            # 预检快照可能保留比当前发送模式更宽的资格；最终创建组件时
            # 必须同时服从任务模式，否则 card_only 会被无关的文本快照拦住。
            return selected.intersection(snapshot["allowed_components"])
        return selected

    @staticmethod
    def _latest_conversation_id(market: str):
        return (
            select(im_conversation.c.conversation_id)
            .where(
                im_conversation.c.bd_market == str(market),
                im_conversation.c.creator_oec_id
                == send_task_lead.c.oec_id,
            )
            .order_by(
                im_conversation.c.last_active_ms.desc().nullslast(),
                im_conversation.c.conversation_id.desc(),
            )
            .limit(1)
            .correlate(send_task_lead)
            .scalar_subquery()
        )

    def _insert_components(
        self,
        connection,
        *,
        task: dict,
        leads: list[dict],
    ) -> int:
        rows = []
        for lead in leads:
            allowed = self._allowed_components(task, lead)
            if "text" in allowed:
                rendered_text = str(
                    lead.get("rendered_text_snapshot") or ""
                ).strip()
                if not rendered_text:
                    raise PreflightStale("preflight_stale:text_snapshot_missing")
                try:
                    unresolved = unresolved_placeholder_names(rendered_text)
                except TemplateValidationError as exc:
                    raise PreflightStale(
                        "preflight_stale:unresolved_template_placeholder"
                    ) from exc
                if unresolved:
                    raise PreflightStale(
                        "preflight_stale:unresolved_template_placeholder"
                    )
            for kind, sequence_no in _SEND_MODE_COMPONENTS[task["send_mode"]]:
                if kind not in allowed:
                    continue
                rows.append(
                    {
                        "component_id": self.id_factory("component"),
                        "lead_id": lead["lead_id"],
                        "component_kind": kind,
                        "sequence_no": sequence_no,
                        "status": "pending",
                        "dedupe_key": f"{lead['lead_id']}:{kind}",
                        "conversation_id": lead.get(
                            "conversation_id_snapshot"
                        ),
                        "attempt_count": 0,
                        "created_at": self.now(),
                        "updated_at": self.now(),
                    }
                )
        if not rows:
            return 0
        inserted = 0
        for offset in range(0, len(rows), _BULK_WRITE_BATCH_SIZE):
            statement = pg_insert(send_task_component).values(rows[offset:offset + _BULK_WRITE_BATCH_SIZE])
            result = connection.execute(
                statement.on_conflict_do_nothing(
                    constraint="uq_send_task_component_kind"
                ).returning(send_task_component.c.component_id)
            )
            inserted += len(result.scalars().all())
        return inserted

    def revalidate_and_reserve(
        self,
        task_id: str,
        *,
        expected_revision: int,
        actor: str,
        im_state_repo,
        attribution_repo,
        selected_accounts: list[str] | tuple[str, ...] = ("acc1",),
        hold_for_http: bool = False,
    ) -> dict:
        """在一个事务里完成最后复核、归因预留、组件创建和任务启动。"""
        from bdhub.hub.repo.send_attribution import (
            ActiveAttributionConflict,
        )

        clean_actor = self._clean_actor(actor)
        clean_accounts = list(dict.fromkeys(
            str(account or "").strip() for account in selected_accounts
        ))
        if not clean_accounts or any(not account for account in clean_accounts):
            raise ValueError("send_task_accounts_required")
        if any(":" in account for account in clean_accounts):
            raise ValueError("invalid_send_task_account")
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            if hold_for_http and (task['bd_market']!='mx' or task['send_mode']!='text_only'):
                raise ValueError('http_batch_mx_text_only')
            if (
                int(task["revision"]) != int(expected_revision)
                or task["preflight_revision"] != task["revision"]
            ):
                raise PreflightStale("preflight_stale:revision")
            if task["status"] != "ready":
                raise TaskStateConflict("task_not_ready")

            leads = [
                dict(row)
                for row in connection.execute(
                    select(
                        send_task_lead,
                        self._latest_conversation_id(
                            task["bd_market"]
                        ).label("conversation_id_snapshot"),
                    ).where(
                        send_task_lead.c.task_id == task_id,
                        send_task_lead.c.eligibility_status.in_(
                            ["eligible", "warning"]
                        ),
                        send_task_lead.c.manual_excluded.is_(False),
                    )
                ).mappings().all()
            ]
            if not leads:
                raise PreflightStale("preflight_stale:no_eligible_leads")

            identity_keys = sorted({
                str(lead["creator_identity_key"] or "").strip()
                for lead in leads
                if str(lead["creator_identity_key"] or "").strip()
            })
            if len(identity_keys) != len({
                str(lead["creator_identity_key"] or "").strip()
                for lead in leads
            }):
                raise PreflightStale("preflight_stale:identity_changed")
            if im_state_repo.rejected_identity_keys(
                market=task["bd_market"],
                identity_keys=identity_keys,
                connection=connection,
            ):
                raise PreflightStale("preflight_stale:rejected_creator")

            try:
                attribution = attribution_repo.reserve_task(
                    task_id=task_id,
                    connection=connection,
                )
            except ActiveAttributionConflict as exc:
                raise ValueError("active_pair_observation") from exc

            component_count = self._insert_components(
                connection,
                task=task,
                leads=leads,
            )
            if component_count <= 0:
                raise PreflightStale("preflight_stale:no_components")

            now = self.now()
            connection.execute(
                delete(send_task_account).where(
                    send_task_account.c.task_id == task_id
                )
            )
            connection.execute(insert(send_task_account), [
                {
                    "task_id": task_id,
                    "account_name": account_name,
                    "assigned_by": clean_actor,
                    "assigned_at": now,
                }
                for account_name in clean_accounts
            ])
            running = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status="paused" if hold_for_http else "running",
                    **({'template_snapshot':{**(task.get('template_snapshot') or {}),'_execution_transport':'pure_http'}} if hold_for_http else {}),
                    started_at=task["started_at"] or now,
                    updated_by=clean_actor,
                    updated_at=now,
                )
                .returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                event_type="task_started",
                actor=clean_actor,
                payload={
                    "revision": task["revision"],
                    "execution_transport": "pure_http" if hold_for_http else "native",
                    "component_count": component_count,
                    "attribution": attribution,
                    "execution_accounts": clean_accounts,
                },
            )
            return {
                "task": self._task_dict(running),
                "component_count": component_count,
                "attribution": attribution,
                "execution_accounts": clean_accounts,
            }

    def quarantine_http_unknown(self, task_id, component_id, *, actor, reason):
        """运营确认隔离指定旧未知项；保留unknown，不推断结果或允许重发。"""
        if not str(reason or '').strip():raise ValueError('quarantine_reason_required')
        with self.engine.begin() as c:
            task=self._locked_task(c,task_id)
            snapshot=dict(task.get('template_snapshot') or {})
            if task['status']!='paused' or snapshot.get('_execution_transport')!='pure_http':
                raise TaskStateConflict('quarantine_requires_paused_http_task')
            component=c.execute(select(send_task_component).join(send_task_lead,send_task_lead.c.lead_id==send_task_component.c.lead_id).where(
                send_task_lead.c.task_id==task_id,send_task_component.c.component_id==component_id).with_for_update(of=send_task_component)).mappings().one()
            if component['status']!='unknown':raise TaskStateConflict('quarantine_requires_unknown')
            ids=list(snapshot.get('_http_quarantined_unknown') or [])
            if component_id not in ids:ids.append(component_id)
            snapshot['_http_quarantined_unknown']=ids
            now=self.now()
            c.execute(update(send_task).where(send_task.c.task_id==task_id).values(template_snapshot=snapshot,updated_by=actor,updated_at=now))
            c.execute(update(send_task_lead).where(send_task_lead.c.lead_id==component['lead_id']).values(manual_excluded=True,updated_at=now))
            self._append_event(c,task_id=task_id,event_type='http_unknown_quarantined',actor=actor,
                payload={'component_id':component_id,'reason':reason,'result':'unknown','automatic_resend_disabled':True})
            return {'component_id':component_id,'status':'unknown','quarantined':True,'automatic_resend_disabled':True}

    @staticmethod
    def _blocking_http_unknown_count(connection, task):
        snapshot=task.get('template_snapshot') or {}
        ids=snapshot.get('_http_quarantined_unknown',[]) if snapshot.get('_execution_transport')=='pure_http' else []
        return connection.execute(select(func.count()).select_from(send_task_component.join(
            send_task_lead,send_task_lead.c.lead_id==send_task_component.c.lead_id)).where(
                send_task_lead.c.task_id==task['task_id'],send_task_component.c.status=='unknown',
                or_(send_task_component.c.component_id.not_in(ids),send_task_lead.c.manual_excluded.is_not(True)))).scalar_one()

    def configure_http_trial(self, task_id, *, expected_revision, accounts, limit, actor, send_interval_seconds=None, product_card=None, resolve_new_conversations=False, component_ids=None, production=False):
        """冻结有限pending组件并绑定HTTP执行账号；不恢复任务，不修改正文/名单。"""
        names=list(dict.fromkeys(accounts))
        if not names or len(names)>4 or any(not isinstance(n,str) or not n or ':' in n for n in names):
            raise ValueError('http_trial_accounts_invalid')
        if type(limit) is not int or not 1<=limit<=500:
            raise ValueError('http_trial_limit_invalid')
        if type(production) is not bool:raise ValueError('http_batch_mode_invalid')
        if component_ids is not None and (not isinstance(component_ids,list) or len(component_ids)!=limit or any(not isinstance(v,str) or not v.startswith('component_') for v in component_ids) or len(set(component_ids))!=len(component_ids)):
            raise ValueError('http_batch_components_invalid')
        if type(resolve_new_conversations) is not bool or (resolve_new_conversations and not production and (limit>3 or len(names)!=1 or product_card is not None)):
            raise ValueError('http_cold_trial_requires_one_account_max_three')
        if send_interval_seconds is not None:
            import math
            if isinstance(send_interval_seconds,bool) or not isinstance(send_interval_seconds,(int,float)) or not math.isfinite(send_interval_seconds) or not 1<=send_interval_seconds<=60:
                raise ValueError('http_trial_interval_invalid')
        with self.engine.begin() as c:
            task=self._locked_task(c,task_id)
            is_card=task['send_mode']=='card_only'
            if is_card and (resolve_new_conversations or production):raise ValueError('http_cold_trial_text_only')
            if task['status']!='paused' or task['send_mode'] not in ('text_only','card_only') or task['bd_market']!='mx':
                raise TaskStateConflict('http_trial_requires_paused_mx_text_task')
            if is_card:
                if limit>3 or len(names)!=1 or not isinstance(product_card,dict) or product_card.get('product_id')!=task['default_pid']:
                    raise ValueError('http_card_trial_requires_fixed_pid_and_small_batch')
                for key in ('product_id','list_id','campaign_id'):
                    value=product_card.get(key)
                    if not isinstance(value,str) or not value.isascii() or not value.isdigit():raise ValueError('http_card_trial_invalid_binding')
            elif product_card is not None:raise ValueError('http_card_trial_mode_mismatch')
            if task['revision']!=expected_revision or task['preflight_revision']!=expected_revision:
                raise PreflightStale('http_trial_revision_changed')
            joined=send_task_component.join(send_task_lead,send_task_component.c.lead_id==send_task_lead.c.lead_id)
            if (self._blocking_http_unknown_count(c,task) or c.execute(select(func.count()).select_from(joined).where(send_task_lead.c.task_id==task_id,
                    send_task_component.c.status=='sending')).scalar_one()):
                raise TaskStateConflict('http_trial_has_unresolved_writes')
            known=exists(select(1).where(im_conversation.c.bd_market==task['bd_market'],
                                       im_conversation.c.creator_oec_id==send_task_lead.c.oec_id))
            conversation_scope=(true() if production else and_(~known,send_task_component.c.conversation_id.is_(None),
                                    send_task_component.c.attempt_count==0)) if resolve_new_conversations else known
            rows=c.execute(select(send_task_component.c.component_id).select_from(joined).where(
                send_task_lead.c.task_id==task_id,send_task_component.c.status=='pending',
                send_task_component.c.component_kind==('card' if is_card else 'text'),send_task_lead.c.manual_excluded.is_(False),
                send_task_lead.c.oec_id.is_not(None),
                send_task_lead.c.pid==task['default_pid'] if is_card else true(),
                conversation_scope,
                send_task_component.c.component_id.in_(component_ids) if component_ids is not None else true(),
            ).order_by(send_task_lead.c.source_row_no,send_task_component.c.component_id).limit(limit)).scalars().all()
            if len(rows)!=limit:raise ValueError('http_trial_insufficient_no_cid_pending' if resolve_new_conversations else 'http_trial_insufficient_known_cid_pending')
            previous=c.execute(select(send_task_account.c.account_name).where(send_task_account.c.task_id==task_id)).scalars().all()
            snapshot=dict(task.get('template_snapshot') or {})
            authorized=snapshot.get('_http_authorized_accounts',previous)
            if not set(names).issubset(authorized):raise ValueError('http_trial_account_not_previously_authorized')
            now=self.now()
            trial={'run_id':'http_'+uuid4().hex,'component_ids':list(rows),'accounts':names,
                   'expires_at':(now+timedelta(minutes=120)).isoformat(),'limit':limit}
            if resolve_new_conversations:trial['resolve_new_conversations']=True
            if production:trial['mode']='production'
            if send_interval_seconds is not None:trial['send_interval_seconds']=float(send_interval_seconds)
            snapshot['_http_authorized_accounts']=authorized
            snapshot['_execution_transport']='pure_http'
            snapshot['_http_trial']=trial
            if is_card:trial['product_card']=dict(product_card)
            c.execute(update(send_task).where(send_task.c.task_id==task_id).values(
                template_snapshot=snapshot,updated_by=actor,updated_at=now))
            # 移出原生监听账号，即使它仍运行旧代码，也不能领取这个任务。
            c.execute(delete(send_task_account).where(send_task_account.c.task_id==task_id))
            c.execute(insert(send_task_account),[{'task_id':task_id,'account_name':n,'assigned_by':actor,'assigned_at':now} for n in names])
            self._append_event(c,task_id=task_id,event_type='http_trial_configured',actor=actor,
                               payload={**trial,'previous_accounts':previous})
            return trial

    def http_batch_state(self, task_id):
        """正式HTTP批次只读预览；无本地CID不等于陌生达人。"""
        with self.engine.connect() as c:
            task=self._task_dict(dict(c.execute(select(send_task).where(send_task.c.task_id==task_id)).mappings().one()))
            snapshot=task.get('template_snapshot') or {};batch=snapshot.get('_http_trial') or {}
            known=or_(send_task_component.c.conversation_id.is_not(None),exists(select(1).where(
                im_conversation.c.bd_market==task['bd_market'],im_conversation.c.creator_oec_id==send_task_lead.c.oec_id)))
            joined=send_task_component.join(send_task_lead,send_task_component.c.lead_id==send_task_lead.c.lead_id)
            eligible=and_(send_task_lead.c.task_id==task_id,send_task_component.c.status=='pending',
                send_task_component.c.component_kind=='text',send_task_lead.c.manual_excluded.is_(False),send_task_lead.c.oec_id.is_not(None))
            count,known_count=c.execute(select(func.count(),func.count().filter(known)).select_from(joined).where(eligible)).one()
            if task['status']=='ready':
                ready_known=exists(select(1).where(im_conversation.c.bd_market==task['bd_market'],im_conversation.c.creator_oec_id==send_task_lead.c.oec_id))
                count,known_count=c.execute(select(func.count(),func.count().filter(ready_known)).select_from(send_task_lead).where(
                    send_task_lead.c.task_id==task_id,send_task_lead.c.eligibility_status.in_(['eligible','warning']),
                    send_task_lead.c.manual_excluded.is_(False),send_task_lead.c.oec_id.is_not(None))).one()
            counts=dict(c.execute(select(send_task_component.c.status,func.count()).where(
                send_task_component.c.component_id.in_(batch.get('component_ids',[]))).group_by(send_task_component.c.status)).all())
            names=snapshot.get('_http_authorized_accounts')
            if names is None:names=list(c.execute(select(send_task_account.c.account_name).where(send_task_account.c.task_id==task_id)).scalars())
            return {'task_id':task_id,'revision':task['revision'],'status':task['status'],'transport':snapshot.get('_execution_transport','native'),
                'supported':task['bd_market']=='mx' and task['send_mode']=='text_only',
                'pending':count,'known_conversations':known_count,'needs_conversation':count-known_count,
                'blocking_unknown':self._blocking_http_unknown_count(c,task),'authorized_accounts':names,
                'batch':batch or None,'batch_counts':counts,'max_batch_size':500,'max_accounts':4}

    def begin_http_conversation(self, claim, *, identity_group, run_id):
        """跨任务按IM身份/OEC串行记单次建会话意图；未决意图永不自动重试。"""
        import hashlib
        from sqlalchemy import text
        scope=hashlib.sha256(('mx:'+identity_group+':'+claim['lead']['oec_id']).encode()).hexdigest()
        with self.engine.begin() as c:
            task=self._locked_task(c,claim['task']['task_id'])
            trial=(task.get('template_snapshot') or {}).get('_http_trial') or {}
            if (task['status']!='running' or trial.get('run_id')!=run_id or not trial.get('resolve_new_conversations')
                    or claim['component']['component_id'] not in trial.get('component_ids',[])):
                raise TaskStateConflict('http_create_trial_changed')
            c.execute(text('select pg_advisory_xact_lock(:key)'),{'key':int(scope[:15],16)})
            prior=c.execute(select(send_task_event).where(send_task_event.c.event_type=='http_conversation_intent',
                send_task_event.c.payload['scope'].astext==scope).limit(1)).mappings().first()
            if prior:raise TaskStateConflict('http_create_prior_intent_requires_verification')
            attempt=c.execute(select(send_component_attempt).where(send_component_attempt.c.attempt_id==claim['attempt']['attempt_id']).with_for_update()).mappings().one()
            if attempt['status']!='sending' or attempt['component_id']!=claim['component']['component_id']:
                raise TaskStateConflict('http_create_attempt_changed')
            intent_id='conversation_'+uuid4().hex
            evidence={'intent_id':intent_id,'scope':scope,'attempt_id':attempt['attempt_id'],
                      'component_id':attempt['component_id'],'run_id':run_id,'state':'started'}
            self._append_event(c,task_id=task['task_id'],lead_id=claim['lead']['lead_id'],
                               event_type='http_conversation_intent',actor='http_trial',payload=evidence)
            c.execute(update(send_component_attempt).where(send_component_attempt.c.attempt_id==attempt['attempt_id']).values(
                transport_snapshot={'conversation_create':evidence}))
            return intent_id

    def finish_http_conversation(self, claim, *, intent_id, result):
        cid=result.get('conversation_id'); is_new=result.get('is_new')
        if not isinstance(cid,str) or not cid.isascii() or not cid.isdigit() or int(cid)<=0 or (is_new is not None and type(is_new) is not bool):
            raise ValueError('http_create_result_invalid')
        with self.engine.begin() as c:
            attempt=c.execute(select(send_component_attempt).where(send_component_attempt.c.attempt_id==claim['attempt']['attempt_id']).with_for_update()).mappings().one()
            evidence=dict((attempt['transport_snapshot'] or {}).get('conversation_create') or {})
            if attempt['status']!='sending' or evidence.get('intent_id')!=intent_id or evidence.get('state')!='started':
                raise TaskStateConflict('http_create_intent_changed')
            evidence.update(state='resolved',**result)
            c.execute(update(send_component_attempt).where(send_component_attempt.c.attempt_id==attempt['attempt_id']).values(
                conversation_id=cid,transport_snapshot={'conversation_create':evidence}))
            c.execute(update(send_task_component).where(send_task_component.c.component_id==attempt['component_id'],
                send_task_component.c.status=='sending').values(conversation_id=cid))
            self._append_event(c,task_id=claim['task']['task_id'],lead_id=claim['lead']['lead_id'],
                event_type='http_conversation_resolved',actor='http_trial',payload=evidence)

    def recover_http_conversation(self, task_id, component_id, *, expected_revision, attempt_id, proof, actor):
        """仅恢复已保存CID且经本账号只读核验、明确零消息POST的会话准备项。"""
        import hashlib
        cid=proof.get('conversation_id')
        if (proof.get('oec_ticket_verified') is not True or not isinstance(cid,str)
                or not cid.isascii() or not cid.isdigit() or int(cid)<=0 or not proof.get('identity_group')):
            raise ValueError('http_conversation_recovery_proof_missing')
        with self.engine.begin() as c:
            task=self._locked_task(c,task_id)
            trial=(task.get('template_snapshot') or {}).get('_http_trial') or {}
            if (task['status']!='paused' or task['revision']!=expected_revision or task['preflight_revision']!=expected_revision
                    or task['bd_market']!='mx' or task['send_mode']!='text_only' or not trial.get('resolve_new_conversations')
                    or (task.get('template_snapshot') or {}).get('_execution_transport')!='pure_http'
                    or component_id not in trial.get('component_ids',[])):
                raise TaskStateConflict('http_conversation_recovery_scope_changed')
            component=c.execute(select(send_task_component).where(send_task_component.c.component_id==component_id).with_for_update()).mappings().one()
            lead=c.execute(select(send_task_lead).where(send_task_lead.c.lead_id==component['lead_id']).with_for_update()).mappings().one()
            attempt=c.execute(select(send_component_attempt).where(send_component_attempt.c.attempt_id==attempt_id).with_for_update()).mappings().one()
            snapshot=dict(attempt['transport_snapshot'] or {});send=snapshot.get('send') or {}
            if (lead['task_id']!=task_id or lead['manual_excluded'] or component['status']!='unknown'
                    or attempt['component_id']!=component_id or attempt['attempt_no']!=component['attempt_count']
                    or attempt['status']!='unknown' or attempt['platform_message_id'] or component['platform_message_id']
                    or send.get('transport_kind')!='pure_http' or type(send.get('http_send_posts')) is not int or send['http_send_posts']!=0
                    or send.get('client_message_id') or send.get('candidate_platform_message_id')
                    or send.get('conversation_create_http_status')!=200 or type(send.get('conversation_create_code')) is not int or send['conversation_create_code']!=0
                    or send.get('error_code')!='http_create_result_unknown' or send.get('candidate_conversation_id')!=cid
                    or send.get('run_id')!=trial.get('run_id')):
                raise TaskStateConflict('http_conversation_recovery_not_proven_unsent')
            scope=hashlib.sha256(('mx:'+proof['identity_group']+':'+lead['oec_id']).encode()).hexdigest()
            intent=c.execute(select(send_task_event.c.payload).where(send_task_event.c.task_id==task_id,
                send_task_event.c.event_type=='http_conversation_intent',
                send_task_event.c.payload['intent_id'].astext==send.get('conversation_intent_id'))).scalar_one()
            if intent.get('scope')!=scope or intent.get('attempt_id')!=attempt_id:
                raise TaskStateConflict('http_conversation_recovery_identity_changed')
            evidence={**intent,'state':'resolved','conversation_id':cid,'is_new':None,'method':'http_readback',
                      'oec_ticket_verified':True,'message_posts':0,'resolved_at':self.now().isoformat()}
            snapshot['http_conversation_readback_resolution']=evidence
            # 旧attempt保留unknown及原始现场；新attempt只发送文字，不再建会话。
            c.execute(update(send_component_attempt).where(send_component_attempt.c.attempt_id==attempt_id).values(transport_snapshot=snapshot))
            c.execute(update(send_task_component).where(send_task_component.c.component_id==component_id).values(
                status='pending',conversation_id=cid,updated_at=self.now()))
            c.execute(update(send_task_lead).where(send_task_lead.c.lead_id==component['lead_id']).values(status='pending',updated_at=self.now()))
            self._append_event(c,task_id=task_id,lead_id=component['lead_id'],event_type='http_conversation_resolved',actor=actor,payload=evidence)
            return {'component_id':component_id,'status':'pending','conversation_id':cid,'message_not_dispatched':True,'task_resumed':False}

    def release_http_trial_undispatched(self, task_id, component_id):
        """只归还因暂停而未调用HTTP的claim；保留失败attempt及明确释放审计。"""
        with self.engine.begin() as c:
            task=self._locked_task(c,task_id)
            if task['status']!='paused':raise TaskStateConflict('http_release_requires_paused_task')
            row=c.execute(select(send_task_component).join(send_task_lead,send_task_lead.c.lead_id==send_task_component.c.lead_id).where(
                send_task_lead.c.task_id==task_id,send_task_component.c.component_id==component_id).with_for_update(of=send_task_component)).mappings().one()
            attempt=c.execute(select(send_component_attempt).where(send_component_attempt.c.component_id==component_id)
                              .order_by(send_component_attempt.c.attempt_no.desc()).limit(1)).mappings().one()
            snapshot=dict(attempt['transport_snapshot'] or {})
            proof=snapshot.get('send',snapshot)
            if (row['status']!='failed_retryable' or attempt['status']!='failed_retryable'
                    or proof.get('transport_kind')!='pure_http' or proof.get('http_send_posts')!=0
                    or proof.get('conversation_intent_id') or proof.get('http_conversation_create_posts')
                    or proof.get('error_code') not in ('http_trial_state_changed','http_trial_stopped_before_dispatch','http_write_gate_timeout')
                    or attempt['platform_message_id'] is not None):
                raise ValueError('http_release_not_proven_undispatched')
            snapshot['not_dispatched_requeued']=True
            c.execute(update(send_component_attempt).where(send_component_attempt.c.attempt_id==attempt['attempt_id']).values(transport_snapshot=snapshot))
            c.execute(update(send_task_component).where(send_task_component.c.component_id==component_id).values(status='pending',updated_at=self.now()))
            self._append_event(c,task_id=task_id,event_type='http_trial_claim_released',actor='http_trial',
                               payload={'component_id':component_id,'attempt_id':attempt['attempt_id'],'http_send_posts':0})

    def pause_http_trial_if_drained(self, task_id, run_id):
        with self.engine.begin() as c:
            task=self._locked_task(c,task_id)
            trial=(task.get('template_snapshot') or {}).get('_http_trial') or {}
            if trial.get('run_id')!=run_id or task['status']!='running':return False
            pending=c.execute(select(func.count()).select_from(send_task_component).where(
                send_task_component.c.component_id.in_(trial['component_ids']),
                send_task_component.c.status.in_(['pending','sending']))).scalar_one()
            if pending:return False
            c.execute(update(send_task).where(send_task.c.task_id==task_id).values(
                status='paused',updated_by='http_trial',updated_at=self.now()))
            self._append_event(c,task_id=task_id,event_type='http_trial_drained',actor='http_trial',payload={'run_id':run_id})
            return True

    def pause_task(self, task_id: str, *, actor: str) -> dict:
        """人工暂停执行；只允许运行中任务进入暂停态。"""
        clean_actor = self._clean_actor(actor)
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            if task["status"] == "paused":
                return task
            if task["status"] != "running":
                raise TaskStateConflict("task_not_running")
            paused = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status="paused",
                    status_reason="paused_by_user",
                    updated_by=clean_actor,
                    updated_at=self.now(),
                )
                .returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                event_type="task_paused",
                actor=clean_actor,
                payload={"reason": "paused_by_user"},
            )
            return self._task_dict(paused)

    def _component_count(
        self,
        connection,
        *,
        task_id: str,
        statuses: set[str],
    ) -> int:
        return int(connection.execute(
            select(func.count())
            .select_from(
                send_task_component.join(
                    send_task_lead,
                    send_task_lead.c.lead_id
                    == send_task_component.c.lead_id,
                )
            )
            .where(
                send_task_lead.c.task_id == task_id,
                send_task_component.c.status.in_(sorted(statuses)),
            )
        ).scalar_one())

    def resume_task(self, task_id: str, *, actor: str) -> dict:
        """恢复人工暂停任务；未知结果或在途 I/O 必须先处理。"""
        clean_actor = self._clean_actor(actor)
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            if task["status"] != "paused":
                raise TaskStateConflict("task_not_paused")
            if self._blocking_http_unknown_count(connection,task):
                raise TaskStateConflict("unresolved_unknown_component")
            if self._component_count(
                connection,
                task_id=task_id,
                statuses={"sending"},
            ):
                raise TaskStateConflict("task_has_inflight_io")
            resumed = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status="running",
                    status_reason=None,
                    updated_by=clean_actor,
                    updated_at=self.now(),
                )
                .returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                event_type="task_resumed",
                actor=clean_actor,
            )
            return self._task_dict(resumed)

    def retry_failed_components(self, task_id: str, *, actor: str) -> dict:
        """显式重试可恢复失败，并解锁同一达人被前序失败阻塞的后续组件。"""
        clean_actor = self._clean_actor(actor)
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            if task["status"] != "completed_with_errors":
                raise TaskStateConflict("task_not_retryable")
            if self._component_count(
                connection,
                task_id=task_id,
                statuses={"unknown", "sending"},
            ):
                raise TaskStateConflict("unresolved_unknown_component")

            retryable_rows = connection.execute(
                select(
                    send_task_component.c.component_id,
                    send_task_component.c.lead_id,
                    send_task_component.c.sequence_no,
                    send_task_component.c.status,
                )
                .select_from(
                    send_task_component.join(
                        send_task_lead,
                        send_task_lead.c.lead_id
                        == send_task_component.c.lead_id,
                    )
                )
                .where(
                    send_task_lead.c.task_id == task_id,
                    send_task_component.c.status == "failed_retryable",
                )
                .order_by(
                    send_task_component.c.lead_id.asc(),
                    send_task_component.c.sequence_no.asc(),
                )
                .with_for_update(of=send_task_component)
            ).mappings().all()
            if not retryable_rows:
                raise TaskStateConflict("no_retryable_components")

            retryable_ids = [
                str(row["component_id"]) for row in retryable_rows
            ]
            retry_update = connection.execute(
                update(send_task_component)
                .where(
                    send_task_component.c.component_id.in_(retryable_ids),
                    send_task_component.c.status == "failed_retryable",
                )
                .values(
                    status="pending",
                    updated_at=self.now(),
                )
            )

            first_retry_sequence: dict[str, int] = {}
            for row in retryable_rows:
                lead_id = str(row["lead_id"])
                sequence_no = int(row["sequence_no"])
                first_retry_sequence[lead_id] = min(
                    sequence_no,
                    first_retry_sequence.get(lead_id, sequence_no),
                )

            unblocked_components = 0
            for lead_id, sequence_no in first_retry_sequence.items():
                result = connection.execute(
                    update(send_task_component)
                    .where(
                        send_task_component.c.lead_id == lead_id,
                        send_task_component.c.sequence_no > sequence_no,
                        send_task_component.c.status
                        == "blocked_by_previous",
                    )
                    .values(status="pending", updated_at=self.now())
                )
                unblocked_components += int(result.rowcount or 0)

            connection.execute(
                update(send_task_lead)
                .where(send_task_lead.c.lead_id.in_(first_retry_sequence))
                .values(status="pending", updated_at=self.now())
            )
            running = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status="running",
                    status_reason="retry_requested",
                    finished_at=None,
                    updated_by=clean_actor,
                    updated_at=self.now(),
                )
                .returning(send_task)
            ).mappings().one()
            retried_components = int(
                retry_update.rowcount
                if retry_update.rowcount is not None
                else len(retryable_ids)
            )
            self._append_event(
                connection,
                task_id=task_id,
                event_type="task_retry_requested",
                actor=clean_actor,
                payload={
                    "retried_components": retried_components,
                    "unblocked_components": unblocked_components,
                },
            )
            return {
                "task": self._task_dict(running),
                "retried_components": retried_components,
                "unblocked_components": unblocked_components,
            }

    def cancel_task(
        self,
        task_id: str,
        *,
        actor: str,
        attribution_repo,
    ) -> dict:
        """取消未完成任务；只释放尚未成功送达的商品卡归因预留。"""
        clean_actor = self._clean_actor(actor)
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            if task["status"] == "cancelled":
                return task
            if task["status"] in {"completed", "completed_with_errors"}:
                raise TaskStateConflict("task_already_finished")
            if self._component_count(
                connection,
                task_id=task_id,
                statuses={"sending"},
            ):
                raise TaskStateConflict("task_has_inflight_io")

            reserved_leads = []
            if get_dashboard_market(
                str(task["bd_market"])
            ).reports_enabled:
                reserved_leads = connection.execute(
                    select(send_task_attribution.c.lead_id).where(
                        send_task_attribution.c.task_id == task_id,
                        send_task_attribution.c.status == "reserved",
                    )
                ).scalars().all()
            for lead_id in reserved_leads:
                attribution_repo.release_unsent(
                    lead_id=str(lead_id),
                    reason="task_cancelled",
                    connection=connection,
                )

            cancelled = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status="cancelled",
                    status_reason="manual_cancel",
                    finished_at=self.now(),
                    updated_by=clean_actor,
                    updated_at=self.now(),
                )
                .returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                event_type="task_cancelled",
                actor=clean_actor,
                payload={"released_reservations": len(reserved_leads)},
            )
            return self._task_dict(cancelled)

    def set_card_override(
        self,
        task_id: str,
        *,
        lead_id: str,
        expected_revision: int,
        enabled: bool,
        reason: str | None,
        actor: str,
    ) -> dict:
        """仅允许对历史已加橱的达人+PID 人工强制再次发卡。"""
        clean_actor = self._clean_actor(actor)
        clean_reason = str(reason or "").strip()
        if enabled and not clean_reason:
            raise ValueError("force_card_reason_required")

        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            self._require_revision(task, expected_revision)
            if task["status"] not in {"draft", "ready"}:
                raise TaskStateConflict("task_not_mutable")
            if task["send_mode"] not in _CARD_MODES:
                raise ValueError("task_has_no_card_component")

            lead_row = connection.execute(
                select(send_task_lead)
                .where(
                    send_task_lead.c.task_id == task_id,
                    send_task_lead.c.lead_id == lead_id,
                )
                .with_for_update()
            ).mappings().one_or_none()
            if lead_row is None:
                raise LookupError("send_task_lead_not_found")
            lead = dict(lead_row)
            snapshot = dict(lead.get("source_snapshot") or {})
            if snapshot.get("active_task_pair"):
                raise ValueError("active_pair_observation")
            if not snapshot.get("tap_pair_present"):
                raise ValueError("card_override_requires_preexisting_pair")

            allowed = [
                kind
                for kind, _sequence_no
                in _SEND_MODE_COMPONENTS[task["send_mode"]]
                if enabled or kind != "card"
            ]
            snapshot.update({
                "force_card": bool(enabled),
                "force_card_reason": clean_reason if enabled else None,
                "allowed_components": allowed,
                "card_attribution_eligible": False,
            })
            connection.execute(
                update(send_task_lead)
                .where(send_task_lead.c.lead_id == lead_id)
                .values(
                    source_snapshot=snapshot,
                    eligibility_status="pending",
                    exclusion_code=None,
                    status="pending",
                    updated_at=self.now(),
                )
            )
            new_revision = int(task["revision"]) + 1
            changed = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status="draft",
                    status_reason=None,
                    revision=new_revision,
                    preflight_revision=None,
                    updated_by=clean_actor,
                    updated_at=self.now(),
                )
                .returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                lead_id=lead_id,
                event_type="preexisting_card_override_changed",
                actor=clean_actor,
                payload={
                    "enabled": bool(enabled),
                    "reason": clean_reason if enabled else None,
                    "revision": new_revision,
                },
            )
            return self._task_dict(changed)

    @staticmethod
    def _resolved_lead_status(statuses: list[str]) -> str:
        if any(status in {"pending", "sending"} for status in statuses):
            return "pending"
        if "unknown" in statuses:
            return "review_required"
        if statuses and all(status == "sent" for status in statuses):
            return "sent"
        if "sent" in statuses:
            return "partial"
        return "failed"

    def resolve_unknown_component(
        self,
        task_id: str,
        component_id: str,
        *,
        resolution: str,
        evidence,
        actor: str,
        attribution_repo,
    ) -> dict:
        """把人工核验结论落到未知组件；不根据空响应推断发送结果。"""
        if resolution not in {"confirmed_sent", "confirmed_not_sent"}:
            raise ValueError("invalid_unknown_resolution")
        if isinstance(evidence, dict):
            clean_evidence = dict(evidence)
        else:
            note = str(evidence or "").strip()
            clean_evidence = {"note": note} if note else {}
        if not any(str(value or "").strip() for value in clean_evidence.values()):
            raise ValueError("manual_evidence_required")
        clean_actor = self._clean_actor(actor)
        resolved_at = self.now()
        confirmed_sent_at = resolved_at
        raw_sent_at = clean_evidence.get("sent_at")
        if resolution == "confirmed_sent" and raw_sent_at:
            if isinstance(raw_sent_at, datetime):
                confirmed_sent_at = raw_sent_at
            else:
                try:
                    confirmed_sent_at = datetime.fromisoformat(
                        str(raw_sent_at).strip().replace("Z", "+00:00")
                    )
                except ValueError as exc:
                    raise ValueError("invalid_confirmed_sent_at") from exc
            if confirmed_sent_at.tzinfo is None:
                raise ValueError("confirmed_sent_at_timezone_required")

        with self.engine.begin() as connection:
            component_row = connection.execute(
                select(send_task_component)
                .where(send_task_component.c.component_id == component_id)
                .with_for_update()
            ).mappings().one_or_none()
            if component_row is None:
                raise LookupError("send_task_component_not_found")
            component = dict(component_row)
            lead_row = connection.execute(
                select(send_task_lead)
                .where(send_task_lead.c.lead_id == component["lead_id"])
                .with_for_update()
            ).mappings().one_or_none()
            if lead_row is None or lead_row["task_id"] != task_id:
                raise LookupError("component_not_in_task")
            lead = dict(lead_row)
            if component["status"] != "unknown":
                raise TaskStateConflict("component_not_unknown")

            attempt_row = connection.execute(
                select(send_component_attempt)
                .where(
                    send_component_attempt.c.component_id == component_id,
                    send_component_attempt.c.status == "unknown",
                )
                .order_by(send_component_attempt.c.attempt_no.desc())
                .with_for_update()
                .limit(1)
            ).mappings().one_or_none()
            if attempt_row is None:
                raise LookupError("unknown_attempt_not_found")
            attempt = dict(attempt_row)
            manual_resolution = {
                "resolution": resolution,
                "evidence": clean_evidence,
                "actor": clean_actor,
                "resolved_at": resolved_at.isoformat(),
            }
            transport_snapshot = dict(
                attempt.get("transport_snapshot") or {}
            )
            transport_snapshot["manual_resolution"] = manual_resolution
            connection.execute(
                update(send_component_attempt)
                .where(
                    send_component_attempt.c.attempt_id
                    == attempt["attempt_id"]
                )
                .values(transport_snapshot=transport_snapshot)
            )

            target_status = (
                "sent"
                if resolution == "confirmed_sent"
                else "failed_retryable"
            )
            if (
                target_status == "sent"
                and component["component_kind"] == "card"
            ):
                attribution_repo.activate_card_success(
                    lead_id=component["lead_id"],
                    sent_at=confirmed_sent_at,
                    connection=connection,
                )

            component_values = {
                "status": target_status,
                "updated_at": resolved_at,
            }
            if target_status == "sent":
                component_values.update({
                    "sent_at": confirmed_sent_at,
                    "platform_message_id": (
                        str(
                            clean_evidence.get("platform_message_id") or ""
                        ).strip()
                        or component.get("platform_message_id")
                    ),
                })
            changed = connection.execute(
                update(send_task_component)
                .where(send_task_component.c.component_id == component_id)
                .values(**component_values)
                .returning(send_task_component)
            ).mappings().one()

            if target_status == "sent":
                connection.execute(
                    update(send_task_component)
                    .where(
                        send_task_component.c.lead_id
                        == component["lead_id"],
                        send_task_component.c.sequence_no
                        > component["sequence_no"],
                        send_task_component.c.status
                        == "blocked_by_previous",
                    )
                    .values(status="pending", updated_at=resolved_at)
                )

            statuses = [
                str(status)
                for status in connection.execute(
                    select(send_task_component.c.status)
                    .where(
                        send_task_component.c.lead_id
                        == component["lead_id"]
                    )
                    .order_by(send_task_component.c.sequence_no.asc())
                ).scalars().all()
            ]
            connection.execute(
                update(send_task_lead)
                .where(
                    send_task_lead.c.lead_id == component["lead_id"]
                )
                .values(
                    status=self._resolved_lead_status(statuses),
                    updated_at=resolved_at,
                )
            )

            remaining_unknown = self._component_count(
                connection,
                task_id=task_id,
                statuses={"unknown"},
            )
            if remaining_unknown == 0:
                connection.execute(
                    update(send_task)
                    .where(
                        send_task.c.task_id == task_id,
                        send_task.c.status == "paused",
                    )
                    .values(
                        status_reason="manual_resume_required",
                        updated_by=clean_actor,
                        updated_at=resolved_at,
                    )
                )
            self._append_event(
                connection,
                task_id=task_id,
                lead_id=lead["lead_id"],
                event_type="unknown_component_resolved",
                actor=clean_actor,
                payload=manual_resolution,
            )
            return dict(changed)

    def compensate_start_failure(
        self,
        task_id: str,
        *,
        actor: str,
        attribution_repo,
    ) -> dict:
        """进程未启动时撤销准备事务；任何组件 I/O 出现后均拒绝回退。"""
        clean_actor = self._clean_actor(actor)
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            if task["status"] != "running":
                raise TaskStateConflict("task_not_running")

            lead_ids = select(send_task_lead.c.lead_id).where(
                send_task_lead.c.task_id == task_id
            )
            component_ids = select(
                send_task_component.c.component_id
            ).where(send_task_component.c.lead_id.in_(lead_ids))
            attempt_count = int(connection.execute(
                select(func.count())
                .select_from(send_component_attempt)
                .where(
                    send_component_attempt.c.component_id.in_(
                        component_ids
                    )
                )
            ).scalar_one())
            if attempt_count:
                raise TaskStateConflict("start_compensation_unsafe")
            non_pending_count = self._component_count(
                connection,
                task_id=task_id,
                statuses={
                    "sending",
                    "sent",
                    "unknown",
                    "failed_retryable",
                    "failed_terminal",
                    "limited",
                    "blocked_by_previous",
                },
            )
            if non_pending_count:
                raise TaskStateConflict("start_compensation_unsafe")

            reports_enabled = get_dashboard_market(
                str(task["bd_market"])
            ).reports_enabled
            reserved_leads = []
            if reports_enabled:
                reserved_leads = connection.execute(
                    select(send_task_attribution.c.lead_id).where(
                        send_task_attribution.c.task_id == task_id,
                        send_task_attribution.c.status == "reserved",
                    )
                ).scalars().all()
            for lead_id in reserved_leads:
                attribution_repo.release_unsent(
                    lead_id=str(lead_id),
                    reason="process_start_failed",
                    connection=connection,
                )
            if reports_enabled:
                connection.execute(
                    delete(send_task_attribution).where(
                        send_task_attribution.c.task_id == task_id
                    )
                )
            connection.execute(
                delete(send_task_component).where(
                    send_task_component.c.lead_id.in_(lead_ids)
                )
            )
            restored = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status="ready",
                    status_reason=None,
                    started_at=None,
                    updated_by=clean_actor,
                    updated_at=self.now(),
                )
                .returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                event_type="task_start_compensated",
                actor=clean_actor,
                payload={
                    "reason": "process_start_failed",
                    "released_reservations": len(reserved_leads),
                },
            )
            return {
                "task": self._task_dict(restored),
                "released_reservations": len(reserved_leads),
            }

    def create_components(self, task_id: str) -> int:
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            leads = connection.execute(
                select(
                    send_task_lead.c.lead_id,
                    send_task_lead.c.rendered_text_snapshot,
                    send_task_lead.c.source_snapshot,
                    self._latest_conversation_id(
                        task["bd_market"]
                    ).label("conversation_id_snapshot"),
                ).where(
                    send_task_lead.c.task_id == task_id,
                    send_task_lead.c.eligibility_status.in_(["eligible", "warning"]),
                    send_task_lead.c.manual_excluded.is_(False),
                )
            ).mappings().all()
            return self._insert_components(
                connection,
                task=task,
                leads=[dict(lead) for lead in leads],
            )

    def _stale_sending_attempt(
        self,
        connection,
        *,
        task_id: str,
    ) -> dict | None:
        cutoff = self.now() - self.attempt_stale_after
        row = connection.execute(
            select(
                send_component_attempt.c.attempt_id,
                send_component_attempt.c.component_id,
                send_component_attempt.c.started_at,
                send_task_component.c.lead_id,
                send_task_lead.c.task_id,
            )
            .select_from(
                send_component_attempt
                .join(
                    send_task_component,
                    send_task_component.c.component_id
                    == send_component_attempt.c.component_id,
                )
                .join(
                    send_task_lead,
                    send_task_lead.c.lead_id
                    == send_task_component.c.lead_id,
                )
            )
            .where(
                send_task_lead.c.task_id == task_id,
                send_component_attempt.c.status == "sending",
                send_task_component.c.status == "sending",
                send_component_attempt.c.started_at <= cutoff,
            )
            .order_by(send_component_attempt.c.started_at.asc())
            .with_for_update(
                of=send_component_attempt,
                skip_locked=True,
            )
            .limit(1)
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    def _mark_stale_unknown(
        self,
        connection,
        *,
        stale: dict,
        reason: str = "stale_sending_attempt",
    ) -> None:
        now = self.now()
        connection.execute(
            update(send_component_attempt)
            .where(
                send_component_attempt.c.attempt_id
                == stale["attempt_id"],
                send_component_attempt.c.status == "sending",
            )
            .values(
                status="unknown",
                finished_at=now,
                error_code=reason,
                error_detail=(
                    "发送进程中断后无法确认平台是否已经接收，禁止自动重发"
                ),
                transport_snapshot={
                    "recovered_at": now.isoformat(),
                    "recovery_reason": reason,
                },
            )
        )
        connection.execute(
            update(send_task_component)
            .where(
                send_task_component.c.component_id
                == stale["component_id"],
                send_task_component.c.status == "sending",
            )
            .values(status="unknown", updated_at=now)
        )
        connection.execute(
            update(send_task_component)
            .where(
                send_task_component.c.lead_id == stale["lead_id"],
                send_task_component.c.component_id
                != stale["component_id"],
                send_task_component.c.status.in_(
                    ["pending", "blocked_by_previous"]
                ),
            )
            .values(status="blocked_by_previous", updated_at=now)
        )
        connection.execute(
            update(send_task_lead)
            .where(send_task_lead.c.lead_id == stale["lead_id"])
            .values(status="review_required", updated_at=now)
        )
        connection.execute(
            update(send_task)
            .where(send_task.c.task_id == stale["task_id"])
            .values(
                status="paused",
                status_reason="unknown_component_review",
                updated_at=now,
            )
        )
        self._append_event(
            connection,
            task_id=stale["task_id"],
            lead_id=stale["lead_id"],
            event_type="component_unknown",
            actor="send_worker",
            payload={
                "component_id": stale["component_id"],
                "attempt_id": stale["attempt_id"],
                "reason": reason,
            },
        )

    def recover_orphaned_attempt_for_worker(
        self,
        worker_id: str,
    ) -> dict | None:
        """隔离同一 worker 上次进程遗留的 sending attempt。"""
        clean_worker_id = str(worker_id or "").strip()
        if not clean_worker_id:
            raise ValueError("worker_id_required")

        with self.engine.begin() as connection:
            row = connection.execute(
                select(
                    send_component_attempt.c.attempt_id,
                    send_component_attempt.c.component_id,
                    send_component_attempt.c.worker_id,
                    send_component_attempt.c.send_account,
                    send_component_attempt.c.started_at,
                    send_task_component.c.lead_id,
                    send_task_lead.c.task_id,
                )
                .select_from(
                    send_component_attempt
                    .join(
                        send_task_component,
                        send_task_component.c.component_id
                        == send_component_attempt.c.component_id,
                    )
                    .join(
                        send_task_lead,
                        send_task_lead.c.lead_id
                        == send_task_component.c.lead_id,
                    )
                )
                .where(
                    send_component_attempt.c.worker_id == clean_worker_id,
                    send_component_attempt.c.status == "sending",
                    send_task_component.c.status == "sending",
                )
                .order_by(send_component_attempt.c.started_at.asc())
                .with_for_update(
                    of=send_component_attempt,
                    skip_locked=True,
                )
                .limit(1)
            ).mappings().one_or_none()
            if row is None:
                return None

            orphaned = dict(row)
            self._mark_stale_unknown(
                connection,
                stale=orphaned,
                reason="orphaned_worker_attempt",
            )
            return orphaned

    @staticmethod
    def _component_claim_statement(
        *,
        task_scope: tuple,
        task_statuses: tuple[str, ...],
        component_statuses: tuple[str, ...],
        include_task_created_order: bool,
        execution_transport: str = 'native',
    ):
        predecessor = send_task_component.alias("predecessor_component")
        ordering = (
            (
                send_task.c.created_at.asc(),
                send_task.c.task_id.asc(),
                send_task_lead.c.source_row_no.asc(),
                send_task_component.c.sequence_no.asc(),
                send_task_component.c.component_id.asc(),
            )
            if include_task_created_order
            else (
                send_task_lead.c.source_row_no.asc(),
                send_task_component.c.sequence_no.asc(),
                send_task_component.c.component_id.asc(),
            )
        )
        known_conversation_id = (
            select(im_conversation.c.conversation_id)
            .where(
                im_conversation.c.bd_market == send_task.c.bd_market,
                im_conversation.c.creator_oec_id == send_task_lead.c.oec_id,
            )
            .order_by(
                im_conversation.c.last_active_ms.desc().nullslast(),
                im_conversation.c.conversation_id.desc(),
            )
            .limit(1)
            .scalar_subquery()
        )
        return (
            select(
                send_task_component,
                known_conversation_id.label("known_conversation_id"),
            )
            .select_from(
                send_task_component
                .join(
                    send_task_lead,
                    send_task_lead.c.lead_id
                    == send_task_component.c.lead_id,
                )
                .join(
                    send_task,
                    send_task.c.task_id == send_task_lead.c.task_id,
                )
            )
            .where(
                *task_scope,
                func.coalesce(send_task.c.template_snapshot['_execution_transport'].astext,'native')==execution_transport,
                send_task.c.status.in_(task_statuses),
                send_task_component.c.status.in_(component_statuses),
                ~exists(
                    select(1).where(
                        predecessor.c.lead_id
                        == send_task_component.c.lead_id,
                        predecessor.c.sequence_no
                        < send_task_component.c.sequence_no,
                        predecessor.c.status != "sent",
                    )
                ),
            )
            .order_by(*ordering)
            .with_for_update(
                of=send_task_component,
                skip_locked=True,
            )
            .limit(1)
        )

    def _start_component_attempt(
        self,
        connection,
        *,
        component: dict,
        candidate_statuses: tuple[str, ...],
        send_account: str | None = None,
        worker_id: str | None = None,
    ) -> tuple[dict, dict]:
        """Persist the common sending-attempt transition for both claim paths."""
        attempt_no = int(component["attempt_count"] or 0) + 1
        attempt_id = self.id_factory("attempt")
        started_at = self.now()
        attempt_values = {
            "attempt_id": attempt_id,
            "component_id": component["component_id"],
            "attempt_no": attempt_no,
            "status": "sending",
            "started_at": started_at,
            "transport_snapshot": {},
        }
        if send_account is not None:
            attempt_values["send_account"] = send_account
        if worker_id is not None:
            attempt_values["worker_id"] = worker_id
        attempt_row = connection.execute(
            insert(send_component_attempt)
            .values(**attempt_values)
            .returning(send_component_attempt)
        ).mappings().one()

        status_clause = (
            send_task_component.c.status == candidate_statuses[0]
            if len(candidate_statuses) == 1
            else send_task_component.c.status.in_(candidate_statuses)
        )
        connection.execute(
            update(send_task_component)
            .where(
                send_task_component.c.component_id
                == component["component_id"],
                status_clause,
            )
            .values(
                status="sending",
                attempt_count=attempt_no,
                updated_at=started_at,
            )
        )
        component.update(
            status="sending",
            attempt_count=attempt_no,
            updated_at=started_at,
        )
        return component, dict(attempt_row)

    def claim_next_component_for_worker(
        self,
        *,
        market: str,
        account_name: str,
        worker_id: str,
        task_id: str | None = None,
        http_trial_run_id: str | None = None,
    ) -> dict | None:
        """从同市场全部已确认任务中原子领取一个待发组件。"""
        clean_market = str(market or "").strip().lower()
        clean_account = str(account_name or "").strip()
        clean_worker_id = str(worker_id or "").strip()
        if not clean_market:
            raise ValueError("market_required")
        if not clean_account:
            raise ValueError("account_name_required")
        if ":" in clean_account:
            raise ValueError("invalid_account_name")
        if not clean_worker_id:
            raise ValueError("worker_id_required")
        if clean_worker_id != f"{clean_market}:{clean_account}":
            raise ValueError("worker_id_mismatch")

        trial_scope=()
        if http_trial_run_id:
            task=self.get_task(task_id)
            trial=((task or {}).get('template_snapshot') or {}).get('_http_trial') or {}
            if (trial.get('run_id')!=http_trial_run_id or clean_account not in trial.get('accounts',[])
                    or datetime.fromisoformat(trial['expires_at'])<=self.now()):
                return None
            trial_scope=(send_task.c.template_snapshot['_http_trial']['run_id'].astext==http_trial_run_id,
                         send_task_component.c.component_id.in_(trial['component_ids']))

        with self.engine.begin() as connection:
            component_row = connection.execute(
                self._component_claim_statement(
                    task_scope=(
                        *trial_scope,
                        send_task.c.bd_market == clean_market,
                        send_task.c.task_id == task_id if task_id is not None else true(),
                        send_task.c.preflight_revision
                        == send_task.c.revision,
                        exists(
                            select(1).where(
                                send_task_account.c.task_id
                                == send_task.c.task_id,
                                send_task_account.c.account_name
                                == clean_account,
                            )
                        ),
                    ),
                    task_statuses=("running",),
                    component_statuses=("pending",),
                    include_task_created_order=True,
                    execution_transport='pure_http' if http_trial_run_id else 'native',
                )
            ).mappings().one_or_none()
            if component_row is None:
                return None

            component = dict(component_row)
            known_conversation_id = component.pop(
                "known_conversation_id",
                None,
            )
            if not component.get("conversation_id") and known_conversation_id:
                component["conversation_id"] = str(known_conversation_id)
            task_row = connection.execute(
                select(send_task).where(
                    send_task.c.task_id
                    == select(send_task_lead.c.task_id)
                    .where(
                        send_task_lead.c.lead_id == component["lead_id"]
                    )
                    .scalar_subquery()
                )
            ).mappings().one_or_none()
            if task_row is None:
                return None
            task = dict(task_row)
            if (
                task["bd_market"] != clean_market
                or task["status"] != "running"
                or task["preflight_revision"] is None
                or int(task["preflight_revision"]) != int(task["revision"])
            ):
                return None

            lead_row = connection.execute(
                select(send_task_lead).where(
                    send_task_lead.c.lead_id == component["lead_id"]
                )
            ).mappings().one()
            lead = dict(lead_row)

            component, attempt = self._start_component_attempt(
                connection,
                component=component,
                candidate_statuses=("pending",),
                send_account=clean_account,
                worker_id=clean_worker_id,
            )
            return {
                "task": task,
                "lead": lead,
                "component": component,
                "attempt": attempt,
            }

    def claim_next_component(
        self,
        task_id: str,
        *,
        retry_failed: bool = False,
    ) -> dict | None:
        """原子认领一个可执行组件，并在任何平台 I/O 前落 sending attempt。"""
        candidate_statuses = (
            ["failed_retryable", "pending"]
            if retry_failed
            else ["pending"]
        )
        task_statuses = (
            ["running", "completed_with_errors"]
            if retry_failed
            else ["running"]
        )

        with self.engine.begin() as connection:
            stale = self._stale_sending_attempt(
                connection,
                task_id=task_id,
            )
            if stale is not None:
                self._mark_stale_unknown(connection, stale=stale)
                return None

            component_row = connection.execute(
                self._component_claim_statement(
                    task_scope=(send_task.c.task_id == task_id,),
                    task_statuses=tuple(task_statuses),
                    component_statuses=tuple(candidate_statuses),
                    include_task_created_order=False,
                )
            ).mappings().one_or_none()
            if component_row is None:
                return None

            component = dict(component_row)
            known_conversation_id = component.pop(
                "known_conversation_id",
                None,
            )
            if not component.get("conversation_id") and known_conversation_id:
                component["conversation_id"] = str(known_conversation_id)
            lead_row = connection.execute(
                select(send_task_lead).where(
                    send_task_lead.c.lead_id == component["lead_id"]
                )
            ).mappings().one()
            task_row = connection.execute(
                select(send_task).where(send_task.c.task_id == task_id)
            ).mappings().one()
            lead = dict(lead_row)
            task = dict(task_row)

            if task["status"] == "completed_with_errors":
                connection.execute(
                    update(send_task)
                    .where(send_task.c.task_id == task_id)
                    .values(
                        status="running",
                        status_reason=None,
                        finished_at=None,
                        updated_at=self.now(),
                    )
                )
                task["status"] = "running"
                task["status_reason"] = None
                task["finished_at"] = None

            component, attempt = self._start_component_attempt(
                connection,
                component=component,
                candidate_statuses=tuple(candidate_statuses),
            )
            return {
                "task": task,
                "lead": lead,
                "component": component,
                "attempt": attempt,
            }

    def begin_attempt(self, component_id: str) -> dict:
        with self.engine.begin() as connection:
            component = connection.execute(
                select(send_task_component)
                .where(send_task_component.c.component_id == component_id)
                .with_for_update()
            ).mappings().one_or_none()
            if component is None:
                raise LookupError("component_not_found")
            if component["status"] == "sent":
                raise ValueError("组件已成功")
            if component["status"] in _NON_RETRYABLE_COMPONENT_STATES:
                raise TaskStateConflict(
                    f"component_not_retryable:{component['status']}"
                )
            _, attempt = self._start_component_attempt(
                connection,
                component=dict(component),
                candidate_statuses=(str(component["status"]),),
            )
            return attempt

    def _finish_attempt_in_connection(
        self,
        connection,
        attempt_id: str,
        *,
        status: str,
        platform_message_id=None,
        conversation_id=None,
        error_code=None,
        error_detail=None,
        transport_snapshot=None,
        actor: str = "send_worker",
        locked_attempt=None,
    ) -> dict:
        attempt = locked_attempt
        if attempt is None:
            attempt = connection.execute(
                select(send_component_attempt)
                .where(send_component_attempt.c.attempt_id == attempt_id)
                .with_for_update()
            ).mappings().one_or_none()
        if attempt is None:
            raise LookupError("attempt_not_found")
        attempt = dict(attempt)
        if attempt["status"] != "sending":
            raise TaskStateConflict("attempt_already_finished")

        component = connection.execute(
            select(send_task_component)
            .where(
                send_task_component.c.component_id
                == attempt["component_id"]
            )
            .with_for_update()
        ).mappings().one()
        component = dict(component)
        if status == "sent" and not platform_message_id:
            raise ValueError("platform_message_id_required")

        now = self.now()
        connection.execute(
            update(send_component_attempt)
            .where(
                send_component_attempt.c.attempt_id == attempt_id,
                send_component_attempt.c.status == "sending",
            )
            .values(
                status=status,
                platform_message_id=platform_message_id,
                conversation_id=conversation_id,
                finished_at=now,
                error_code=error_code,
                error_detail=error_detail,
                transport_snapshot=transport_snapshot or {},
            )
        )
        component_row = connection.execute(
            update(send_task_component)
            .where(
                send_task_component.c.component_id
                == component["component_id"],
                send_task_component.c.status == "sending",
            )
            .values(
                status=status,
                platform_message_id=platform_message_id,
                conversation_id=conversation_id,
                sent_at=now if status == "sent" else None,
                updated_at=now,
            )
            .returning(send_task_component)
        ).mappings().one()

        successor_status = (
            "pending" if status == "sent" else "blocked_by_previous"
        )
        successor_states = (
            ["blocked_by_previous"]
            if status == "sent"
            else ["pending", "blocked_by_previous"]
        )
        connection.execute(
            update(send_task_component)
            .where(
                send_task_component.c.lead_id == component["lead_id"],
                send_task_component.c.sequence_no
                > component["sequence_no"],
                send_task_component.c.status.in_(successor_states),
            )
            .values(status=successor_status, updated_at=now)
        )

        if status == "unknown":
            connection.execute(
                update(send_task_lead)
                .where(send_task_lead.c.lead_id == component["lead_id"])
                .values(status="review_required", updated_at=now)
            )
            connection.execute(
                update(send_task)
                .where(
                    send_task.c.task_id
                    == select(send_task_lead.c.task_id)
                    .where(
                        send_task_lead.c.lead_id == component["lead_id"]
                    )
                    .scalar_subquery()
                )
                .values(
                    status="paused",
                    status_reason="unknown_component_review",
                    updated_at=now,
                )
            )

        task_id = connection.execute(
            select(send_task_lead.c.task_id).where(
                send_task_lead.c.lead_id == component["lead_id"]
            )
        ).scalar_one()
        self._append_event(
            connection,
            task_id=task_id,
            lead_id=component["lead_id"],
            event_type=(
                "component_unknown"
                if status == "unknown"
                else "component_finished"
            ),
            actor=actor,
            payload={
                "component_id": component["component_id"],
                "attempt_id": attempt_id,
                "status": status,
            },
        )
        return dict(component_row)

    def finish_attempt(
        self,
        attempt_id,
        *,
        status,
        platform_message_id=None,
        conversation_id=None,
        error_code=None,
        error_detail=None,
        transport_snapshot=None,
        actor="send_worker",
    ) -> dict:
        if status not in _ATTEMPT_STATUSES:
            raise ValueError("invalid_attempt_status")
        clean_actor = self._clean_actor(actor)
        with self.engine.begin() as connection:
            return self._finish_attempt_in_connection(
                connection,
                attempt_id,
                status=status,
                platform_message_id=platform_message_id,
                conversation_id=conversation_id,
                error_code=error_code,
                error_detail=error_detail,
                transport_snapshot=transport_snapshot,
                actor=clean_actor,
            )

    def mark_attempt_unknown(
        self,
        attempt_id: str,
        transport_snapshot=None,
        *,
        error_code: str = "platform_result_unknown",
        error_detail: str | None = None,
        actor: str = "send_worker",
    ) -> dict:
        return self.finish_attempt(
            attempt_id,
            status="unknown",
            error_code=error_code,
            error_detail=error_detail,
            transport_snapshot=transport_snapshot,
            actor=actor,
        )

    def mark_component_result(
        self,
        component_id: str,
        outcome,
        *,
        platform_message_id=None,
        conversation_id=None,
        error_code=None,
        error_detail=None,
        transport_snapshot=None,
        actor: str = "send_worker",
    ) -> dict:
        """按逻辑组件完成当前 sending attempt；兼容 runner 的组件级调用。"""
        if isinstance(outcome, dict):
            payload = dict(outcome)
            status = payload.pop("status", None)
            platform_message_id = payload.pop(
                "platform_message_id",
                platform_message_id,
            )
            conversation_id = payload.pop(
                "conversation_id",
                conversation_id,
            )
            error_code = payload.pop("error_code", error_code)
            error_detail = payload.pop("error_detail", error_detail)
            transport_snapshot = payload.pop(
                "transport_snapshot",
                transport_snapshot,
            )
            if payload:
                raise ValueError("unknown_component_outcome_fields")
        else:
            status = outcome
        if status not in _ATTEMPT_STATUSES:
            raise ValueError("invalid_attempt_status")
        clean_actor = self._clean_actor(actor)

        with self.engine.begin() as connection:
            attempt = connection.execute(
                select(send_component_attempt)
                .where(
                    send_component_attempt.c.component_id == component_id,
                    send_component_attempt.c.status == "sending",
                )
                .order_by(send_component_attempt.c.attempt_no.desc())
                .with_for_update()
                .limit(1)
            ).mappings().one_or_none()
            if attempt is None:
                raise LookupError("active_attempt_not_found")
            return self._finish_attempt_in_connection(
                connection,
                attempt["attempt_id"],
                status=status,
                platform_message_id=platform_message_id,
                conversation_id=conversation_id,
                error_code=error_code,
                error_detail=error_detail,
                transport_snapshot=transport_snapshot,
                actor=clean_actor,
                locked_attempt=attempt,
            )

    def finalize_lead(self, lead_id: str) -> dict:
        with self.engine.begin() as connection:
            lead_row = connection.execute(
                select(send_task_lead)
                .where(send_task_lead.c.lead_id == lead_id)
                .with_for_update()
            ).mappings().one_or_none()
            if lead_row is None:
                raise LookupError("send_task_lead_not_found")
            lead = dict(lead_row)
            components = connection.execute(
                select(send_task_component.c.status)
                .where(send_task_component.c.lead_id == lead_id)
                .order_by(send_task_component.c.sequence_no.asc())
            ).mappings().all()
            statuses = [str(row["status"]) for row in components]
            if not statuses:
                target_status = "skipped"
            elif any(
                status in {"pending", "sending"}
                for status in statuses
            ):
                return lead
            elif "unknown" in statuses:
                target_status = "review_required"
            elif all(status == "sent" for status in statuses):
                target_status = "sent"
            elif "sent" in statuses:
                target_status = "partial"
            elif "limited" in statuses:
                target_status = "limited"
            else:
                target_status = "failed"

            if lead.get("status") == target_status:
                return lead
            now = self.now()
            updated = connection.execute(
                update(send_task_lead)
                .where(send_task_lead.c.lead_id == lead_id)
                .values(status=target_status, updated_at=now)
                .returning(send_task_lead)
            ).mappings().one()
            if target_status == "review_required":
                connection.execute(
                    update(send_task)
                    .where(send_task.c.task_id == lead["task_id"])
                    .values(
                        status="paused",
                        status_reason="unknown_component_review",
                        updated_at=now,
                    )
                )
            self._append_event(
                connection,
                task_id=lead["task_id"],
                lead_id=lead_id,
                event_type="lead_finalized",
                actor="send_worker",
                payload={
                    "status": target_status,
                    "component_statuses": statuses,
                },
            )
            return dict(updated)

    def finalize_task(self, task_id: str) -> dict:
        with self.engine.begin() as connection:
            task = self._locked_task(connection, task_id)
            if task["status"] == "paused":
                return task

            component_rows = connection.execute(
                select(send_task_component.c.status)
                .select_from(
                    send_task_component.join(
                        send_task_lead,
                        send_task_lead.c.lead_id
                        == send_task_component.c.lead_id,
                    )
                )
                .where(send_task_lead.c.task_id == task_id)
            ).mappings().all()
            statuses = [str(row["status"]) for row in component_rows]

            if any(status in {"pending", "sending"} for status in statuses):
                return task
            if "unknown" in statuses:
                target_status = "paused"
                status_reason = "unknown_component_review"
                finished_at = None
            else:
                target_status = (
                    "completed"
                    if statuses and all(
                        status == "sent" for status in statuses
                    )
                    else "completed_with_errors"
                )
                status_reason = (
                    None
                    if target_status == "completed"
                    else "component_errors"
                )
                finished_at = self.now()

            updated = connection.execute(
                update(send_task)
                .where(send_task.c.task_id == task_id)
                .values(
                    status=target_status,
                    status_reason=status_reason,
                    finished_at=finished_at,
                    updated_at=self.now(),
                )
                .returning(send_task)
            ).mappings().one()
            self._append_event(
                connection,
                task_id=task_id,
                event_type=(
                    "task_paused"
                    if target_status == "paused"
                    else "task_finalized"
                ),
                actor="send_worker",
                payload={
                    "status": target_status,
                    "component_statuses": statuses,
                },
            )
            return self._task_dict(updated)

    def append_event(
        self,
        *,
        task_id,
        event_type,
        actor,
        lead_id=None,
        payload=None,
        connection=None,
    ) -> str:
        clean_actor = self._clean_actor(actor)
        if connection is not None:
            return self._append_event(
                connection,
                task_id=task_id,
                event_type=event_type,
                actor=clean_actor,
                lead_id=lead_id,
                payload=payload,
            )
        with self.engine.begin() as own_connection:
            return self._append_event(
                own_connection,
                task_id=task_id,
                event_type=event_type,
                actor=clean_actor,
                lead_id=lead_id,
                payload=payload,
            )


__all__ = ["PreflightStale", "SendTaskRepo", "TaskStateConflict", "build_task_source_summary"]
