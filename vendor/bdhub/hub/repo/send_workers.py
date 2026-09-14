"""常驻 IM 发送 worker 的运行状态仓储。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bdhub.hub.schema import (
    send_task,
    send_task_lead,
    send_worker_state,
)


VALID_WORKER_STATUSES = frozenset(
    {
        "starting",
        "ready",
        "busy",
        "paused",
        "auth_required",
        "unhealthy",
        "stopped",
    }
)
_ERROR_DETAIL_LIMIT = 1000
_RESUMABLE_WORKER_STATUSES = frozenset(
    {"paused", "auth_required", "unhealthy"}
)
_STALE_CANDIDATE_STATUSES = VALID_WORKER_STATUSES - {"stopped"}


class SendWorkerRepo:
    def __init__(
        self,
        engine,
        *,
        now=None,
        stale_after: timedelta = timedelta(seconds=90),
    ):
        if stale_after.total_seconds() <= 0:
            raise ValueError("invalid_stale_after")
        self.engine = engine
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.stale_after = stale_after

    @staticmethod
    def _worker_id(worker_id: object) -> str:
        clean_worker_id = str(worker_id or "").strip()
        if not clean_worker_id:
            raise ValueError("worker_id_required")
        return clean_worker_id

    @staticmethod
    def _market(market: object) -> str:
        clean_market = str(market or "").strip().lower()
        if not clean_market:
            raise ValueError("market_required")
        return clean_market

    @staticmethod
    def _account_name(account_name: object) -> str:
        clean_account = str(account_name or "").strip()
        if not clean_account:
            raise ValueError("account_name_required")
        if ":" in clean_account:
            raise ValueError("invalid_account_name")
        return clean_account

    @staticmethod
    def _process_id(process_id: object) -> int:
        if isinstance(process_id, bool):
            raise ValueError("invalid_process_id")
        try:
            clean_process_id = int(process_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_process_id") from exc
        if clean_process_id <= 0:
            raise ValueError("invalid_process_id")
        return clean_process_id

    @staticmethod
    def _status(status: object) -> str:
        clean_status = str(status or "").strip()
        if clean_status not in VALID_WORKER_STATUSES:
            raise ValueError("invalid_worker_status")
        return clean_status

    @staticmethod
    def _optional_text(value: object, *, limit: int | None = None) -> str | None:
        if value is None:
            return None
        clean_value = str(value).strip()
        if not clean_value:
            return None
        if limit is not None:
            clean_value = clean_value[:limit]
        return clean_value

    @staticmethod
    def _row_or_not_found(result) -> dict:
        row = result.mappings().one_or_none()
        if row is None:
            raise LookupError("send_worker_not_found")
        return dict(row)

    def _decorate(self, row: dict | None) -> dict | None:
        """保留数据库原状态，并单独给出按心跳计算的真实运行状态。"""
        if row is None:
            return None
        result = dict(row)
        effective_status = str(result.get("status") or "")
        heartbeat_at = result.get("heartbeat_at")
        heartbeat_age_seconds: int | None = None
        if isinstance(heartbeat_at, datetime):
            if heartbeat_at.tzinfo is None:
                heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
            heartbeat_age_seconds = max(
                0,
                int((self.now() - heartbeat_at).total_seconds()),
            )
            if (
                effective_status in _STALE_CANDIDATE_STATUSES
                and heartbeat_age_seconds
                > self.stale_after.total_seconds()
            ):
                effective_status = "stale"
        elif effective_status in _STALE_CANDIDATE_STATUSES:
            effective_status = "stale"
        result["effective_status"] = effective_status
        result["heartbeat_age_seconds"] = heartbeat_age_seconds
        result["operational"] = (
            effective_status in {"ready", "busy"}
            and heartbeat_age_seconds is not None
        )
        return result

    def register(
        self,
        worker_id: str,
        market: str,
        account_name: str,
        process_id: int,
    ) -> dict:
        clean_worker_id = self._worker_id(worker_id)
        clean_market = self._market(market)
        clean_account = self._account_name(account_name)
        clean_process_id = self._process_id(process_id)
        if clean_worker_id != f"{clean_market}:{clean_account}":
            raise ValueError("worker_id_mismatch")

        now = self.now()
        statement = pg_insert(send_worker_state).values(
            worker_id=clean_worker_id,
            bd_market=clean_market,
            account_name=clean_account,
            status="starting",
            process_id=clean_process_id,
            current_task_id=None,
            current_lead_id=None,
            heartbeat_at=now,
            started_at=now,
            last_error_code=None,
            last_error_detail=None,
            updated_at=now,
        )
        incoming = statement.excluded
        with self.engine.begin() as connection:
            result = connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["worker_id"],
                    set_={
                        "bd_market": incoming.bd_market,
                        "account_name": incoming.account_name,
                        "status": incoming.status,
                        "process_id": incoming.process_id,
                        "current_task_id": None,
                        "current_lead_id": None,
                        "heartbeat_at": incoming.heartbeat_at,
                        "started_at": incoming.started_at,
                        "last_error_code": None,
                        "last_error_detail": None,
                        "updated_at": incoming.updated_at,
                    },
                ).returning(send_worker_state)
            )
            row = self._row_or_not_found(result)
        return self._decorate(row)

    def heartbeat(
        self,
        worker_id: str,
        *,
        status: str,
        task_id: str | None = None,
        lead_id: str | None = None,
    ) -> dict:
        clean_worker_id = self._worker_id(worker_id)
        clean_status = self._status(status)
        clean_task_id = self._optional_text(task_id)
        clean_lead_id = self._optional_text(lead_id)
        if clean_lead_id is not None and clean_task_id is None:
            raise ValueError("lead_requires_task")
        now = self.now()
        statement = (
            update(send_worker_state)
            .where(send_worker_state.c.worker_id == clean_worker_id)
            .values(
                status=clean_status,
                current_task_id=clean_task_id,
                current_lead_id=clean_lead_id,
                heartbeat_at=now,
                updated_at=now,
            )
            .returning(send_worker_state)
        )
        with self.engine.begin() as connection:
            row = self._row_or_not_found(connection.execute(statement))
        return self._decorate(row)

    def touch(self, worker_id: str) -> dict:
        """只刷新存活时间，不重写人工控制后的状态。"""
        clean_worker_id = self._worker_id(worker_id)
        now = self.now()
        statement = (
            update(send_worker_state)
            .where(send_worker_state.c.worker_id == clean_worker_id)
            .values(
                heartbeat_at=now,
                updated_at=now,
            )
            .returning(send_worker_state)
        )
        with self.engine.begin() as connection:
            row = self._row_or_not_found(connection.execute(statement))
        return self._decorate(row)

    def pause(
        self,
        worker_id: str,
        *,
        code: str | None = None,
        detail: str | None = None,
    ) -> dict:
        clean_worker_id = self._worker_id(worker_id)
        now = self.now()
        statement = (
            update(send_worker_state)
            .where(send_worker_state.c.worker_id == clean_worker_id)
            .values(
                status="paused",
                heartbeat_at=now,
                last_error_code=self._optional_text(code, limit=100),
                last_error_detail=self._optional_text(
                    detail,
                    limit=_ERROR_DETAIL_LIMIT,
                ),
                updated_at=now,
            )
            .returning(send_worker_state)
        )
        with self.engine.begin() as connection:
            row = self._row_or_not_found(connection.execute(statement))
        return self._decorate(row)

    def resume(self, worker_id: str) -> dict:
        """恢复仍在运行进程里的暂停 worker，并清空旧位置与错误。"""
        clean_worker_id = self._worker_id(worker_id)
        now = self.now()
        statement = (
            update(send_worker_state)
            .where(
                send_worker_state.c.worker_id == clean_worker_id,
                send_worker_state.c.status.in_(
                    _RESUMABLE_WORKER_STATUSES
                ),
            )
            .values(
                status="ready",
                current_task_id=None,
                current_lead_id=None,
                heartbeat_at=now,
                last_error_code=None,
                last_error_detail=None,
                updated_at=now,
            )
            .returning(send_worker_state)
        )
        with self.engine.begin() as connection:
            row = self._row_or_not_found(connection.execute(statement))
        return self._decorate(row)

    def stop(self, worker_id: str) -> dict:
        clean_worker_id = self._worker_id(worker_id)
        now = self.now()
        statement = (
            update(send_worker_state)
            .where(send_worker_state.c.worker_id == clean_worker_id)
            .values(
                status="stopped",
                process_id=None,
                current_task_id=None,
                current_lead_id=None,
                heartbeat_at=now,
                updated_at=now,
            )
            .returning(send_worker_state)
        )
        with self.engine.begin() as connection:
            row = self._row_or_not_found(connection.execute(statement))
        return self._decorate(row)

    def get(self, worker_id: str) -> dict | None:
        clean_worker_id = self._worker_id(worker_id)
        statement = select(send_worker_state).where(
            send_worker_state.c.worker_id == clean_worker_id
        )
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().one_or_none()
        return self._decorate(dict(row) if row is not None else None)

    def list_workers(self, market: str | None = None) -> list[dict]:
        worker_view = send_worker_state.outerjoin(
            send_task,
            send_worker_state.c.current_task_id == send_task.c.task_id,
        ).outerjoin(
            send_task_lead,
            send_worker_state.c.current_lead_id
            == send_task_lead.c.lead_id,
        )
        statement = select(
            send_worker_state,
            send_task.c.name.label("current_task_name"),
            send_task_lead.c.raw_handle.label("current_handle"),
        ).select_from(worker_view)
        if market is not None:
            statement = statement.where(
                send_worker_state.c.bd_market == self._market(market)
            )
        statement = statement.order_by(
            send_worker_state.c.account_name,
            send_worker_state.c.worker_id,
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [self._decorate(dict(row)) for row in rows]


__all__ = ["SendWorkerRepo", "VALID_WORKER_STATUSES"]
