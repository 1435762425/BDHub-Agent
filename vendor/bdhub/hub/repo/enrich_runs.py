# -*- coding: utf-8 -*-
"""Light 富化 run/item/attempt 的持久化仓储。"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bdhub.enrich.workflow import ItemState, TransitionDecision
from bdhub.hub.outcome import OutcomeKind
from bdhub.hub.schema import (
    creator_profile_current,
    enrich_attempt,
    enrich_item,
    enrich_run,
)


_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_ATTEMPT_FIELDS = {
    "attempt_id",
    "account",
    "route",
    "outcome_kind",
    "remote_code",
    "detail_code",
    "request_ms",
    "limiter_wait_ms",
    "parse_ms",
    "field_count",
    "quality_status",
    "started_at",
    "finished_at",
}
_REQUIRED_ATTEMPT_FIELDS = {
    "account",
    "route",
    "outcome_kind",
    "started_at",
    "finished_at",
}
_SENSITIVE_PARAMETER_KEYS = {
    "api_key",
    "authorization",
    "client_secret",
    "cookie",
    "cookies",
    "headers",
    "password",
    "passwd",
    "refresh_token",
    "secret",
    "token",
    "access_token",
}
_RUN_STATUSES = {
    "queued",
    "running",
    "paused",
    "cancelled",
    "completed",
    "failed",
}
_EXECUTABLE_RUN_STATUS = "running"
_CANCELLABLE_ITEM_STATUSES = (
    ItemState.QUEUED.value,
    ItemState.RUNNING.value,
    ItemState.RETRY_WAIT.value,
)
# enrich_item 当前每行绑定 9 个字段。固定为 1,000 行既远低于
# PostgreSQL 65,535 参数上限，也避免万人级任务创建一条超大 SQL。
_ENQUEUE_BATCH_SIZE = 1_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _enum_value(value) -> str:
    return str(value.value if isinstance(value, Enum) else value)


def sanitize_attempt_code(value) -> str | None:
    """只允许短协议标识，防止远程 message/body 进入持久层。"""
    if value is None:
        return None
    normalized = str(value)
    if not _SAFE_CODE.fullmatch(normalized):
        raise ValueError("attempt code 不安全")
    return normalized


def _validate_run_parameters(value) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = str(key).strip().casefold().replace("-", "_")
            if normalized_key in _SENSITIVE_PARAMETER_KEYS:
                raise ValueError("运行参数禁止包含敏感字段")
            _validate_run_parameters(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _validate_run_parameters(child)


def build_claim_statement(run_id: str, now: datetime):
    """构造最早可运行项的行锁查询，供多 worker 竞争。"""
    return build_claim_batch_statement(run_id, now, limit=1)


def build_claim_batch_statement(run_id: str, now: datetime, *, limit: int):
    """批量锁定最早可运行项；同一事务内随后转为 running。"""
    if limit <= 0:
        raise ValueError("claim limit 必须大于 0")
    eligible = or_(
        enrich_item.c.status == ItemState.QUEUED.value,
        and_(
            enrich_item.c.status == ItemState.RETRY_WAIT.value,
            enrich_item.c.retry_at <= now,
        ),
    )
    return (
        select(enrich_item.c.item_id)
        .where(enrich_item.c.run_id == run_id, eligible)
        .order_by(enrich_item.c.created_at, enrich_item.c.item_id)
        .with_for_update(skip_locked=True)
        .limit(limit)
    )


def _locked_run_status(connection, run_id: str) -> str | None:
    """锁定 run 行，使认领、暂停和取消按同一顺序串行化。"""
    return connection.execute(
        select(enrich_run.c.status)
        .where(enrich_run.c.run_id == run_id)
        .with_for_update()
    ).scalar()


def _cancelled_nonterminal_counts(connection) -> dict[str, int]:
    rows = connection.execute(
        select(enrich_item.c.status, func.count())
        .select_from(enrich_item.join(
            enrich_run,
            enrich_run.c.run_id == enrich_item.c.run_id,
        ))
        .where(
            enrich_run.c.status == "cancelled",
            enrich_item.c.status.in_(_CANCELLABLE_ITEM_STATUSES),
        )
        .group_by(enrich_item.c.status)
    )
    return {str(status): int(count) for status, count in rows}


@dataclass(frozen=True, slots=True)
class CancelledItemRepair:
    before: dict[str, int]
    updated: int
    after: dict[str, int]


class EnrichRunStore:
    def __init__(self, engine):
        self.engine = engine

    def create_run(
        self,
        market,
        capture_kind,
        route,
        parameters,
        *,
        now: datetime | None = None,
        run_id: str | None = None,
    ) -> str:
        created_at = now or _now()
        new_run_id = run_id or uuid4().hex
        _validate_run_parameters(parameters)
        with self.engine.begin() as connection:
            connection.execute(insert(enrich_run).values(
                run_id=new_run_id,
                bd_market=str(market).strip().lower(),
                capture_kind=_enum_value(capture_kind),
                route=sanitize_attempt_code(route),
                status="queued",
                parameters=dict(parameters),
                created_at=created_at,
            ))
        return new_run_id

    def enqueue(self, run_id, rows, *, now: datetime | None = None) -> int:
        created_at = now or _now()
        payload = [
            {
                "item_id": uuid4().hex,
                "run_id": run_id,
                "bd_market": str(row["bd_market"]).strip().lower(),
                "oec_id": str(row["oec_id"]).strip(),
                "requested_handle": row.get("requested_handle"),
                "capture_kind": _enum_value(row["capture_kind"]),
                "status": ItemState.QUEUED.value,
                "created_at": created_at,
                "updated_at": created_at,
            }
            for row in rows
        ]
        if not payload:
            return 0
        if any(not row["oec_id"] for row in payload):
            raise ValueError("oec_id 不能为空")
        inserted_count = 0
        with self.engine.begin() as connection:
            for offset in range(0, len(payload), _ENQUEUE_BATCH_SIZE):
                batch = payload[offset:offset + _ENQUEUE_BATCH_SIZE]
                statement = (
                    pg_insert(enrich_item)
                    .values(batch)
                    .on_conflict_do_nothing(
                        constraint="uq_enrich_item_work"
                    )
                    .returning(enrich_item.c.item_id)
                )
                inserted_item_ids = (
                    connection.execute(statement).scalars().all()
                )
                inserted_count += len(inserted_item_ids)
        return inserted_count

    def claim_next(self, run_id, worker, now: datetime):
        worker_name = sanitize_attempt_code(worker)
        with self.engine.begin() as connection:
            if _locked_run_status(connection, run_id) != _EXECUTABLE_RUN_STATUS:
                return None
            item_id = connection.execute(
                build_claim_statement(run_id, now)
            ).scalar()
            if item_id is None:
                return None
            row = connection.execute(
                update(enrich_item)
                .where(
                    enrich_item.c.item_id == item_id,
                    enrich_item.c.status.in_((
                        ItemState.QUEUED.value,
                        ItemState.RETRY_WAIT.value,
                    )),
                )
                .values(
                    status=ItemState.RUNNING.value,
                    claimed_by=worker_name,
                    claimed_at=now,
                    retry_at=None,
                    terminal_reason=None,
                    updated_at=now,
                    total_attempts=enrich_item.c.total_attempts + 1,
                )
                .returning(enrich_item)
            ).mappings().one()
            return dict(row)

    def claim_batch(
        self,
        run_id,
        worker,
        now: datetime,
        *,
        limit: int,
    ) -> list[dict]:
        worker_name = sanitize_attempt_code(worker)
        statement = build_claim_batch_statement(run_id, now, limit=limit)
        with self.engine.begin() as connection:
            if _locked_run_status(connection, run_id) != _EXECUTABLE_RUN_STATUS:
                return []
            item_ids = list(connection.execute(statement).scalars().all())
            if not item_ids:
                return []
            rows = connection.execute(
                update(enrich_item)
                .where(
                    enrich_item.c.item_id.in_(item_ids),
                    enrich_item.c.status.in_((
                        ItemState.QUEUED.value,
                        ItemState.RETRY_WAIT.value,
                    )),
                )
                .values(
                    status=ItemState.RUNNING.value,
                    claimed_by=worker_name,
                    claimed_at=now,
                    retry_at=None,
                    terminal_reason=None,
                    updated_at=now,
                    total_attempts=enrich_item.c.total_attempts + 1,
                )
                .returning(enrich_item)
            ).mappings().all()
            if len(rows) != len(item_ids):
                raise RuntimeError("批量工作项状态转换冲突")
            return [dict(row) for row in rows]

    def record_attempt(self, item_id, attempt) -> str:
        values = dict(attempt)
        unknown = set(values) - _ATTEMPT_FIELDS
        if unknown:
            raise ValueError(f"attempt 未知字段: {sorted(unknown)}")
        missing = _REQUIRED_ATTEMPT_FIELDS - set(values)
        if missing:
            raise ValueError(f"attempt 缺少字段: {sorted(missing)}")

        outcome_kind = _enum_value(values["outcome_kind"])
        if outcome_kind not in {kind.value for kind in OutcomeKind}:
            raise ValueError("outcome_kind 非法")
        values["attempt_id"] = values.get("attempt_id") or uuid4().hex
        values["item_id"] = item_id
        values["account"] = sanitize_attempt_code(values["account"])
        values["route"] = sanitize_attempt_code(values["route"])
        values["outcome_kind"] = outcome_kind
        values["remote_code"] = sanitize_attempt_code(
            values.get("remote_code")
        )
        values["detail_code"] = sanitize_attempt_code(
            values.get("detail_code")
        )
        with self.engine.begin() as connection:
            connection.execute(insert(enrich_attempt).values(**values))
        return str(values["attempt_id"])

    def retry(
        self,
        item_id,
        decision: TransitionDecision,
        retry_at: datetime,
        *,
        now: datetime | None = None,
    ) -> None:
        if decision.state is not ItemState.RETRY_WAIT:
            raise ValueError("retry 只接受 RETRY_WAIT 决策")
        values = {
            "status": ItemState.RETRY_WAIT.value,
            "retry_at": retry_at,
            "terminal_reason": sanitize_attempt_code(decision.reason),
            "claimed_by": None,
            "claimed_at": None,
            "updated_at": now or _now(),
        }
        if decision.consume_error_retry:
            values["error_retries"] = enrich_item.c.error_retries + 1
        if decision.consume_persistence_retry:
            values["persistence_retries"] = (
                enrich_item.c.persistence_retries + 1
            )
        self._transition(item_id, {ItemState.RUNNING}, values)

    def succeed(
        self,
        item_id,
        snapshot_id,
        *,
        now: datetime | None = None,
    ) -> None:
        self._transition(item_id, {ItemState.RUNNING}, {
            "status": ItemState.SUCCEEDED.value,
            "snapshot_id": str(snapshot_id),
            "retry_at": None,
            "terminal_reason": None,
            "claimed_by": None,
            "claimed_at": None,
            "updated_at": now or _now(),
        })

    def terminate(
        self,
        item_id,
        state: ItemState,
        reason,
        *,
        now: datetime | None = None,
    ) -> None:
        if state not in {ItemState.UNRESOLVED, ItemState.DEAD_LETTER}:
            raise ValueError("非法终态")
        self._transition(
            item_id,
            {ItemState.RUNNING, ItemState.RETRY_WAIT},
            {
                "status": state.value,
                "retry_at": None,
                "terminal_reason": sanitize_attempt_code(reason),
                "claimed_by": None,
                "claimed_at": None,
                "updated_at": now or _now(),
            },
        )

    def requeue_terminal(
        self,
        run_id,
        reasons,
        *,
        now: datetime | None = None,
    ) -> list[dict]:
        """将指定原因的 unresolved 项原子重开，供显式备用路由续跑。"""
        if isinstance(reasons, (str, bytes)):
            raise ValueError("requeue_terminal reasons 必须是原因集合")
        normalized = {
            sanitize_attempt_code(reason)
            for reason in reasons
        }
        if not normalized or None in normalized:
            raise ValueError("requeue_terminal reasons 不能为空")
        reopened_at = now or _now()
        statement = (
            update(enrich_item)
            .where(
                enrich_item.c.run_id == run_id,
                enrich_item.c.status == ItemState.UNRESOLVED.value,
                enrich_item.c.terminal_reason.in_(sorted(normalized)),
            )
            .values(
                status=ItemState.QUEUED.value,
                retry_at=None,
                terminal_reason=None,
                claimed_by=None,
                claimed_at=None,
                updated_at=reopened_at,
            )
            .returning(
                enrich_item.c.item_id,
                enrich_item.c.oec_id,
                enrich_item.c.requested_handle,
            )
        )
        with self.engine.begin() as connection:
            rows = connection.execute(statement).mappings().all()
            return [dict(row) for row in rows]

    def release_stale_claims(
        self,
        run_id,
        older_than: datetime,
        *,
        now: datetime | None = None,
    ) -> int:
        recovered_at = now or _now()
        with self.engine.begin() as connection:
            result = connection.execute(
                update(enrich_item)
                .where(
                    enrich_item.c.run_id == run_id,
                    enrich_item.c.status == ItemState.RUNNING.value,
                    enrich_item.c.claimed_at < older_than,
                )
                .values(
                    status=ItemState.RETRY_WAIT.value,
                    retry_at=recovered_at,
                    claimed_by=None,
                    claimed_at=None,
                    terminal_reason="stale_claim",
                    updated_at=recovered_at,
                )
            )
            return int(result.rowcount or 0)

    def pause_run(
        self,
        run_id,
        *,
        now: datetime | None = None,
    ) -> int:
        """暂停 run，并立即释放其中所有处理中工作项供断点继续。"""
        paused_at = now or _now()
        with self.engine.begin() as connection:
            current_status = _locked_run_status(connection, run_id)
            if current_status is None:
                raise RuntimeError("富化 run 不存在")
            if current_status == "paused":
                return 0
            if current_status in {"cancelled", "completed", "failed"}:
                raise RuntimeError("富化 run 当前状态不能暂停")
            released = connection.execute(
                update(enrich_item)
                .where(
                    enrich_item.c.run_id == run_id,
                    enrich_item.c.status == ItemState.RUNNING.value,
                )
                .values(
                    status=ItemState.RETRY_WAIT.value,
                    retry_at=paused_at,
                    claimed_by=None,
                    claimed_at=None,
                    terminal_reason="operator_paused",
                    updated_at=paused_at,
                )
            )
            run_result = connection.execute(
                update(enrich_run)
                .where(enrich_run.c.run_id == run_id)
                .values(status="paused", finished_at=None)
            )
            if run_result.rowcount != 1:
                raise RuntimeError("富化 run 暂停状态转换冲突")
            return int(released.rowcount or 0)

    def cancel_run(
        self,
        run_id,
        *,
        now: datetime | None = None,
    ) -> int:
        """把全部非终态 item 原子取消；取消后的任务不能再次被认领。"""
        cancelled_at = now or _now()
        with self.engine.begin() as connection:
            current_status = _locked_run_status(connection, run_id)
            if current_status is None:
                raise RuntimeError("富化 run 不存在")
            cancelled = connection.execute(
                update(enrich_item)
                .where(
                    enrich_item.c.run_id == run_id,
                    enrich_item.c.status.in_(_CANCELLABLE_ITEM_STATUSES),
                )
                .values(
                    status=ItemState.CANCELLED.value,
                    retry_at=None,
                    claimed_by=None,
                    claimed_at=None,
                    terminal_reason="operator_cancelled",
                    updated_at=cancelled_at,
                )
            )
            if current_status != "cancelled":
                run_result = connection.execute(
                    update(enrich_run)
                    .where(enrich_run.c.run_id == run_id)
                    .values(status="cancelled", finished_at=cancelled_at)
                )
                if run_result.rowcount != 1:
                    raise RuntimeError("富化 run 取消状态转换冲突")
            return int(cancelled.rowcount or 0)

    def cancelled_nonterminal_counts(self) -> dict[str, int]:
        """只读统计已取消 run 下仍可执行的历史残留项。"""
        with self.engine.connect() as connection:
            return _cancelled_nonterminal_counts(connection)

    def repair_cancelled_items(
        self,
        *,
        expected_total: int,
        now: datetime | None = None,
    ) -> CancelledItemRepair:
        """幂等终结全部已取消 run 的历史残留项，并核对修复范围。"""
        if expected_total < 0:
            raise ValueError("expected_total 不能小于 0")
        repaired_at = now or _now()
        with self.engine.begin() as connection:
            # 与正常认领/取消使用同一父行锁；不会触碰 paused/running run。
            connection.execute(
                select(enrich_run.c.run_id)
                .where(enrich_run.c.status == "cancelled")
                .with_for_update()
            ).scalars().all()
            before = _cancelled_nonterminal_counts(connection)
            actual_total = sum(before.values())
            if actual_total != expected_total:
                raise RuntimeError(
                    "已取消任务残留数发生变化："
                    f"预期 {expected_total}，实际 {actual_total}；未执行修复"
                )

            cancelled_runs = select(enrich_run.c.run_id).where(
                enrich_run.c.status == "cancelled"
            )
            result = connection.execute(
                update(enrich_item)
                .where(
                    enrich_item.c.run_id.in_(cancelled_runs),
                    enrich_item.c.status.in_(_CANCELLABLE_ITEM_STATUSES),
                )
                .values(
                    status=ItemState.CANCELLED.value,
                    retry_at=None,
                    claimed_by=None,
                    claimed_at=None,
                    terminal_reason="operator_cancelled",
                    updated_at=repaired_at,
                )
            )
            after = _cancelled_nonterminal_counts(connection)
            if after:
                raise RuntimeError("已取消任务残留修复后仍存在非终态项目")
            updated = int(result.rowcount or 0)
            if updated != actual_total:
                raise RuntimeError(
                    "已取消任务残留修复数量不一致："
                    f"应修复 {actual_total}，实际 {updated}"
                )
            return CancelledItemRepair(before, updated, after)

    def counts(self, run_id) -> dict[str, int]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(enrich_item.c.status, func.count())
                .where(enrich_item.c.run_id == run_id)
                .group_by(enrich_item.c.status)
            )
            return {str(status): int(count) for status, count in rows}

    def terminal_reason_counts(self, run_id) -> dict[str, int]:
        """按脱敏原因统计未补全项，供两个工作台解释“失败”数字。"""
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    enrich_item.c.terminal_reason,
                    func.count(),
                )
                .where(
                    enrich_item.c.run_id == run_id,
                    enrich_item.c.status.in_((
                        ItemState.UNRESOLVED.value,
                        ItemState.DEAD_LETTER.value,
                    )),
                )
                .group_by(enrich_item.c.terminal_reason)
            )
            return {
                str(reason or "unresolved"): int(count)
                for reason, count in rows
            }

    def terminal_profile_availability(self, run_id) -> dict[str, int]:
        """区分“刷新失败但旧画像仍在”和“当前完全无画像”。"""
        source = enrich_item.outerjoin(
            creator_profile_current,
            and_(
                creator_profile_current.c.bd_market
                == enrich_item.c.bd_market,
                creator_profile_current.c.oec_id == enrich_item.c.oec_id,
            ),
        )
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    func.count().label("failed"),
                    func.count(creator_profile_current.c.oec_id).label(
                        "existing_profile"
                    ),
                )
                .select_from(source)
                .where(
                    enrich_item.c.run_id == run_id,
                    enrich_item.c.status.in_((
                        ItemState.UNRESOLVED.value,
                        ItemState.DEAD_LETTER.value,
                    )),
                )
            ).mappings().one()
        failed = int(row["failed"] or 0)
        existing = int(row["existing_profile"] or 0)
        return {
            "failed_with_existing_profile": existing,
            "failed_without_profile": max(failed - existing, 0),
        }

    def current_item(self, run_id) -> dict | None:
        """返回正在处理的达人；无 running 时返回最近完成的一位。"""
        running_first = (
            enrich_item.c.status == ItemState.RUNNING.value
        ).desc()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    enrich_item.c.requested_handle,
                    enrich_item.c.oec_id,
                    enrich_item.c.status,
                    enrich_item.c.updated_at,
                )
                .where(
                    enrich_item.c.run_id == run_id,
                    enrich_item.c.status.in_([
                        ItemState.RUNNING.value,
                        ItemState.SUCCEEDED.value,
                        ItemState.UNRESOLVED.value,
                        ItemState.DEAD_LETTER.value,
                    ]),
                )
                .order_by(running_first, enrich_item.c.updated_at.desc())
                .limit(1)
            ).mappings().first()
            return dict(row) if row is not None else None

    def retry_summary(self, run_id) -> dict | None:
        """返回当前最早可重试时间与脱敏暂停原因。"""
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    enrich_item.c.retry_at,
                    enrich_item.c.terminal_reason,
                )
                .where(
                    enrich_item.c.run_id == run_id,
                    enrich_item.c.status == ItemState.RETRY_WAIT.value,
                )
                .order_by(
                    enrich_item.c.retry_at.asc().nullslast(),
                    enrich_item.c.updated_at.desc(),
                )
                .limit(1)
            ).mappings().first()
        if row is None:
            return None
        retry_at = row.get("retry_at")
        return {
            "pause_reason": str(row.get("terminal_reason") or "retry_wait"),
            "retry_at": retry_at.isoformat() if retry_at is not None else None,
        }

    def terminal_items(self, run_id, states) -> list[dict]:
        normalized = {
            state.value if isinstance(state, ItemState) else str(state)
            for state in states
        }
        allowed = {
            ItemState.UNRESOLVED.value,
            ItemState.DEAD_LETTER.value,
        }
        if not normalized or not normalized <= allowed:
            raise ValueError("terminal_items 只接受 unresolved/dead_letter")
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    enrich_item.c.item_id,
                    enrich_item.c.oec_id,
                    enrich_item.c.requested_handle,
                    enrich_item.c.status,
                    enrich_item.c.terminal_reason,
                )
                .where(
                    enrich_item.c.run_id == run_id,
                    enrich_item.c.status.in_(sorted(normalized)),
                )
                .order_by(enrich_item.c.created_at, enrich_item.c.item_id)
            ).mappings().all()
            return [dict(row) for row in rows]

    def set_run_status(
        self,
        run_id,
        status: str,
        *,
        now: datetime | None = None,
    ) -> None:
        if status not in _RUN_STATUSES:
            raise ValueError("run status 非法")
        changed_at = now or _now()
        values = {"status": status}
        if status == "running":
            values["started_at"] = func.coalesce(
                enrich_run.c.started_at,
                changed_at,
            )
            values["finished_at"] = None
        elif status in {"cancelled", "completed", "failed"}:
            values["finished_at"] = changed_at
        elif status == "paused":
            values["finished_at"] = None
        with self.engine.begin() as connection:
            statement = update(enrich_run).where(enrich_run.c.run_id == run_id)
            if status != "cancelled":
                # 迟到的 worker 收尾不能把人工取消的 run 改回 paused/completed。
                statement = statement.where(enrich_run.c.status != "cancelled")
            result = connection.execute(statement.values(**values))
            if result.rowcount != 1:
                current_status = connection.execute(
                    select(enrich_run.c.status).where(
                        enrich_run.c.run_id == run_id
                    )
                ).scalar()
                if current_status == "cancelled" and status != "cancelled":
                    return
                raise RuntimeError("富化 run 状态转换冲突")

    def get_run(self, run_id):
        with self.engine.connect() as connection:
            row = connection.execute(
                select(enrich_run).where(enrich_run.c.run_id == run_id)
            ).mappings().first()
            return dict(row) if row is not None else None

    def _transition(self, item_id, allowed, values) -> None:
        allowed_values = [
            state.value if isinstance(state, ItemState) else str(state)
            for state in allowed
        ]
        with self.engine.begin() as connection:
            result = connection.execute(
                update(enrich_item)
                .where(
                    enrich_item.c.item_id == item_id,
                    enrich_item.c.status.in_(allowed_values),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise RuntimeError("工作项状态转换冲突")
