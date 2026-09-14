# -*- coding: utf-8 -*-
"""OEC 身份画像快照仓库与逐字段 current 投影。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..profile_observation import (
    CaptureContext,
    CaptureKind,
    ProfileAssessment,
    SnapshotQuality,
    assess_profile,
)
from ..models import CreatorProfile, FIELDS
from ..schema import (
    creator_handle_alias,
    creator_identity,
    creator_profile,
    creator_profile_current,
    creator_profile_snapshot,
)


_CURRENT_PROFILE_FIELDS = frozenset(FIELDS) - {"handle", "oec_id"}


def next_peak_gmv(
    previous_value,
    previous_at,
    observed_value,
    observed_at,
):
    """只在官方画像观测给出更高 GMV 时推进历史峰值。"""
    if observed_value is None:
        return previous_value, previous_at
    if previous_value is None or Decimal(str(observed_value)) > Decimal(
        str(previous_value)
    ):
        return observed_value, observed_at
    return previous_value, previous_at


def _json_compatible(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _aware_time(value, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text_value = str(value or "").strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text_value)
        except ValueError as exc:
            raise ValueError(f"{field_name} 不是合法 ISO 时间: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} 必须带时区")
    return parsed.astimezone(timezone.utc)


def merge_current_values(
    existing,
    field_sources,
    incoming,
    valid_fields,
    snapshot_id,
    observed_at,
):
    """仅以时间不早于当前来源的有效字段推进 current，永不修改输入对象。"""
    valid_fields = frozenset(valid_fields)
    unknown = valid_fields - _CURRENT_PROFILE_FIELDS
    if unknown:
        raise ValueError(f"valid_fields 含未知字段: {sorted(unknown)}")
    missing = valid_fields - set(incoming)
    if missing:
        raise ValueError(f"incoming 缺少已声明有效字段: {sorted(missing)}")
    if not str(snapshot_id or "").strip():
        raise ValueError("snapshot_id 不能为空")

    observed_time = _aware_time(observed_at, field_name="observed_at")
    observed_stamp = observed_time.isoformat()
    merged = dict(existing or {})
    sources = dict(field_sources or {})
    for field_name in sorted(valid_fields):
        previous = sources.get(field_name) or {}
        previous_stamp = previous.get("observed_at")
        if previous_stamp is not None:
            previous_time = _aware_time(
                previous_stamp,
                field_name=f"field_sources.{field_name}.observed_at",
            )
            if previous_time > observed_time:
                continue
        merged[field_name] = incoming[field_name]
        sources[field_name] = {
            "snapshot_id": snapshot_id,
            "observed_at": observed_stamp,
        }
    return merged, sources


@dataclass(frozen=True, slots=True)
class IngestResult:
    snapshot_id: str
    quality: SnapshotQuality
    deduplicated: bool
    handle_changed: bool
    observed_handle_key: str = ""
    captured_at: datetime | None = None


class CreatorIdentityConflict(RuntimeError):
    """同一市场的 active handle 已绑定另一 OEC。"""


class SnapshotIdentityMismatch(RuntimeError):
    """幂等键已存在，但快照身份与本次 durable 工作项不一致。"""


class SnapshotReplayInvalid(ValueError):
    """已存快照无法按当前 schema 安全重放。"""


class ProfileSnapshotStore:
    def __init__(self, engine):
        self.engine = engine

    def ingest(
        self,
        handle_key: str,
        profile,
        context: CaptureContext,
        *,
        refresh_legacy: bool = True,
    ) -> IngestResult:
        """在一个事务中落快照，并按质量推进 identity/alias/current/legacy cache。"""
        with self.engine.begin() as connection:
            return self._ingest(
                connection,
                handle_key,
                profile,
                context,
                refresh_legacy,
            )

    def ingest_many(
        self,
        items,
        *,
        refresh_legacy: bool = True,
    ) -> list[IngestResult]:
        """在一个有界事务中顺序摄取多条，供 legacy backfill 批处理。"""
        with self.engine.begin() as connection:
            return [
                self._ingest(
                    connection,
                    handle_key,
                    profile,
                    context,
                    refresh_legacy,
                )
                for handle_key, profile, context in items
            ]

    def reconcile_existing(
        self,
        idempotency_key: str,
        *,
        expected_market: str | None = None,
        expected_oec: str | None = None,
        refresh_legacy: bool = False,
    ) -> IngestResult:
        """从已存快照重放身份/别名/current 投影，不新增或改写事实快照。"""
        result = self.reconcile_if_present(
            idempotency_key,
            expected_market=expected_market,
            expected_oec=expected_oec,
            refresh_legacy=refresh_legacy,
        )
        if result is None:
            raise ValueError(f"找不到待重放快照: {idempotency_key!r}")
        return result

    def reconcile_if_present(
        self,
        idempotency_key: str,
        *,
        expected_market: str | None = None,
        expected_oec: str | None = None,
        refresh_legacy: bool = False,
    ) -> IngestResult | None:
        """幂等重放已存快照；尚未落快照时返回 ``None``。"""
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("idempotency_key 不能为空")
        with self.engine.begin() as connection:
            row = connection.execute(
                select(creator_profile_snapshot).where(
                    creator_profile_snapshot.c.idempotency_key == key
                )
            ).mappings().first()
            if not row:
                return None

            self._verify_snapshot_identity(
                row,
                expected_market=expected_market,
                expected_oec=expected_oec,
            )
            try:
                return self._reconcile_row(
                    connection,
                    row,
                    key,
                    refresh_legacy=refresh_legacy,
                )
            except SQLAlchemyError:
                raise
            except Exception as exc:
                # 已提交事实的格式/投影错误是单项坏数据；不得打断整批或
                # 被当作瞬时数据库故障无限重领。进程级中断仍不会被吞掉。
                raise SnapshotReplayInvalid("snapshot_replay_invalid") from exc

    def _reconcile_row(
        self,
        connection,
        row,
        key: str,
        *,
        refresh_legacy: bool,
    ) -> IngestResult:
        quality = SnapshotQuality(row["quality_status"])
        assessment = ProfileAssessment(
            quality=quality,
            valid_fields=frozenset(row["valid_fields"] or ()),
            rejection_reasons=tuple(row["rejection_reasons"] or ()),
        )
        snapshot_id = str(row["snapshot_id"])
        if (
            quality is SnapshotQuality.DEGRADED
            or not {"handle", "oec_id"} <= assessment.valid_fields
        ):
            return IngestResult(
                snapshot_id,
                quality,
                True,
                False,
                str(row["observed_handle_key"] or ""),
                _aware_time(row["captured_at"], field_name="captured_at"),
            )

        handle_key = str(row["observed_handle_key"])
        values = {
            "handle": str(row["observed_handle"] or handle_key),
            "oec_id": str(row["oec_id"]),
            **{
                field_name: row[field_name]
                for field_name in _CURRENT_PROFILE_FIELDS
            },
        }
        profile = CreatorProfile(
            **values,
            raw=(row["raw_json"] if isinstance(row["raw_json"], dict) else {}),
        )
        context = CaptureContext(
            market=str(row["bd_market"]),
            kind=CaptureKind(row["capture_kind"]),
            route=str(row["route"]),
            expected_oec=str(row["oec_id"]),
            idempotency_key=key,
            captured_at=_aware_time(row["captured_at"], field_name="captured_at"),
            required_fields=frozenset({"handle", "oec_id"}),
        )
        return self._project_snapshot(
            connection,
            snapshot_id,
            handle_key,
            profile,
            context,
            assessment,
            refresh_legacy=refresh_legacy,
            deduplicated=True,
        )

    @staticmethod
    def _verify_snapshot_identity(
        row,
        *,
        expected_market: str | None,
        expected_oec: str | None,
    ) -> None:
        market = str(expected_market or "").strip().lower()
        oec_id = str(expected_oec or "").strip()
        if market and str(row["bd_market"]).strip().lower() != market:
            raise SnapshotIdentityMismatch("snapshot_market_mismatch")
        if oec_id and str(row["oec_id"]).strip() != oec_id:
            raise SnapshotIdentityMismatch("snapshot_oec_mismatch")

    def _ingest(
        self,
        connection,
        handle_key: str,
        profile,
        context: CaptureContext,
        refresh_legacy: bool,
    ) -> IngestResult:
        handle_key = str(handle_key or "").strip()
        if not handle_key or not str(profile.oec_id or "").strip():
            raise ValueError("快照必须有 handle_key 和 oec_id")
        assessment = assess_profile(profile, context)
        snapshot_id = uuid4().hex

        existing = connection.execute(
            select(
                creator_profile_snapshot.c.snapshot_id,
                creator_profile_snapshot.c.quality_status,
                creator_profile_snapshot.c.bd_market,
                creator_profile_snapshot.c.oec_id,
                creator_profile_snapshot.c.observed_handle_key,
                creator_profile_snapshot.c.captured_at,
            ).where(
                creator_profile_snapshot.c.idempotency_key
                == context.idempotency_key
            )
        ).mappings().first()
        if existing:
            self._verify_snapshot_identity(
                existing,
                expected_market=context.market,
                expected_oec=str(profile.oec_id),
            )
            return IngestResult(
                snapshot_id=str(existing["snapshot_id"]),
                quality=SnapshotQuality(existing["quality_status"]),
                deduplicated=True,
                handle_changed=False,
                observed_handle_key=str(
                    existing["observed_handle_key"] or handle_key
                ),
                captured_at=_aware_time(
                    existing["captured_at"],
                    field_name="captured_at",
                ),
            )

        self._insert_snapshot(
            connection,
            snapshot_id,
            handle_key,
            profile,
            context,
            assessment,
        )
        if assessment.quality is SnapshotQuality.DEGRADED:
            return IngestResult(
                snapshot_id,
                assessment.quality,
                False,
                False,
                handle_key,
                context.captured_at,
            )
        if not {"handle", "oec_id"} <= assessment.valid_fields:
            return IngestResult(
                snapshot_id,
                assessment.quality,
                False,
                False,
                handle_key,
                context.captured_at,
            )

        return self._project_snapshot(
            connection,
            snapshot_id,
            handle_key,
            profile,
            context,
            assessment,
            refresh_legacy=refresh_legacy,
            deduplicated=False,
        )

    def _project_snapshot(
        self,
        connection,
        snapshot_id,
        handle_key,
        profile,
        context,
        assessment,
        *,
        refresh_legacy: bool,
        deduplicated: bool,
    ) -> IngestResult:
        handle_changed, advance_handle, identity_handle_key = (
            self._upsert_identity_and_alias(
                connection,
                snapshot_id,
                handle_key,
                profile,
                context,
            )
        )
        current = self._refresh_current(
            connection,
            snapshot_id,
            handle_key,
            profile,
            context,
            assessment,
            advance_handle=advance_handle,
            identity_handle_key=identity_handle_key,
        )
        if refresh_legacy:
            self._refresh_legacy(connection, current, context)
        return IngestResult(
            snapshot_id,
            assessment.quality,
            deduplicated,
            handle_changed,
            handle_key,
            context.captured_at,
        )

    @staticmethod
    def _insert_snapshot(
        connection,
        snapshot_id,
        handle_key,
        profile,
        context,
        assessment,
    ) -> None:
        parsed = profile.to_row()
        parsed.pop("raw_json", None)
        raw = profile.raw if isinstance(profile.raw, dict) else {}
        canonical_raw = json.dumps(
            raw,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        values = {
            "snapshot_id": snapshot_id,
            "idempotency_key": context.idempotency_key,
            "bd_market": context.market,
            "oec_id": str(profile.oec_id),
            "observed_handle_key": handle_key,
            "observed_handle": str(profile.handle or handle_key),
            "capture_kind": context.kind.value,
            "route": context.route,
            "quality_status": assessment.quality.value,
            "valid_fields": sorted(assessment.valid_fields),
            "rejection_reasons": list(assessment.rejection_reasons),
            "parsed_json": _json_compatible(parsed),
            "raw_json": _json_compatible(raw),
            "payload_hash": hashlib.sha256(canonical_raw.encode("utf-8")).hexdigest(),
            "captured_at": context.captured_at,
        }
        values.update({
            field_name: getattr(profile, field_name)
            for field_name in _CURRENT_PROFILE_FIELDS
        })
        connection.execute(insert(creator_profile_snapshot).values(**values))

    def _upsert_identity_and_alias(
        self,
        connection,
        snapshot_id,
        handle_key,
        profile,
        context,
    ) -> tuple[bool, bool, str]:
        identity = connection.execute(
            select(creator_identity)
            .where(
                creator_identity.c.bd_market == context.market,
                creator_identity.c.oec_id == str(profile.oec_id),
            )
            .with_for_update()
        ).mappings().first()

        if identity:
            last_seen_at = identity["last_seen_at"]
            current_handle = str(identity.get("current_handle_key") or "")
            if context.captured_at < last_seen_at:
                self._record_historical_alias(
                    connection,
                    snapshot_id,
                    context.market,
                    str(profile.oec_id),
                    handle_key,
                    str(profile.handle or handle_key),
                    context.captured_at,
                    last_seen_at,
                )
                first_seen_at = identity.get("first_seen_at")
                if first_seen_at is not None and context.captured_at < first_seen_at:
                    connection.execute(
                        update(creator_identity)
                        .where(
                            creator_identity.c.bd_market == context.market,
                            creator_identity.c.oec_id == str(profile.oec_id),
                        )
                        .values(first_seen_at=context.captured_at)
                    )
                return False, False, current_handle
            if (
                context.captured_at == last_seen_at
                and current_handle != handle_key
                and context.kind is not CaptureKind.LEGACY_IMPORT
            ):
                return False, False, current_handle
        else:
            current_handle = ""

        handle_changed = bool(identity and current_handle != handle_key)
        if not identity or handle_changed:
            self._ensure_handle_available(
                connection,
                context.market,
                handle_key,
                str(profile.oec_id),
            )

        if not identity:
            timestamps = self._identity_timestamps(context)
            connection.execute(insert(creator_identity).values(
                bd_market=context.market,
                oec_id=str(profile.oec_id),
                current_handle_key=handle_key,
                first_seen_at=context.captured_at,
                last_seen_at=context.captured_at,
                created_at=context.captured_at,
                updated_at=context.captured_at,
                **timestamps,
            ))
            self._insert_alias(
                connection,
                snapshot_id,
                context.market,
                str(profile.oec_id),
                handle_key,
                str(profile.handle or handle_key),
                context.captured_at,
            )
            return False, True, handle_key

        if handle_changed:
            connection.execute(
                update(creator_handle_alias)
                .where(
                    creator_handle_alias.c.bd_market == context.market,
                    creator_handle_alias.c.oec_id == str(profile.oec_id),
                    creator_handle_alias.c.valid_to.is_(None),
                )
                .values(valid_to=context.captured_at)
            )
            self._insert_alias(
                connection,
                snapshot_id,
                context.market,
                str(profile.oec_id),
                handle_key,
                str(profile.handle or handle_key),
                context.captured_at,
            )
        else:
            connection.execute(
                update(creator_handle_alias)
                .where(
                    creator_handle_alias.c.bd_market == context.market,
                    creator_handle_alias.c.oec_id == str(profile.oec_id),
                    creator_handle_alias.c.valid_to.is_(None),
                )
                .values(confirmed_snapshot_id=snapshot_id)
            )

        identity_values = {
            "current_handle_key": handle_key,
            "last_seen_at": context.captured_at,
            "updated_at": context.captured_at,
            **self._identity_timestamps(context),
        }
        connection.execute(
            update(creator_identity)
            .where(
                creator_identity.c.bd_market == context.market,
                creator_identity.c.oec_id == str(profile.oec_id),
            )
            .values(**identity_values)
        )
        return handle_changed, True, handle_key

    def _record_historical_alias(
        self,
        connection,
        snapshot_id: str,
        market: str,
        oec_id: str,
        handle_key: str,
        handle: str,
        captured_at: datetime,
        last_seen_at: datetime,
    ) -> None:
        """记录迟到的 handle 事实，但绝不替换当前 active alias。"""
        existing = connection.execute(
            select(
                creator_handle_alias.c.alias_id,
                creator_handle_alias.c.valid_from,
            )
            .where(
                creator_handle_alias.c.bd_market == market,
                creator_handle_alias.c.oec_id == oec_id,
                creator_handle_alias.c.handle_key == handle_key,
            )
            .order_by(creator_handle_alias.c.valid_from.asc())
            .limit(1)
            .with_for_update()
        ).mappings().first()
        if existing:
            valid_from = existing.get("valid_from")
            if valid_from is not None and captured_at < valid_from:
                connection.execute(
                    update(creator_handle_alias)
                    .where(creator_handle_alias.c.alias_id == existing["alias_id"])
                    .values(valid_from=captured_at)
                )
            return

        self._insert_alias(
            connection,
            snapshot_id,
            market,
            oec_id,
            handle_key,
            handle,
            captured_at,
            valid_to=last_seen_at,
        )

    @staticmethod
    def _identity_timestamps(context: CaptureContext) -> dict:
        if context.kind is CaptureKind.LIGHT:
            return {"last_light_at": context.captured_at}
        if context.kind is CaptureKind.FULL:
            return {"last_full_at": context.captured_at}
        return {}

    @staticmethod
    def _ensure_handle_available(
        connection,
        market: str,
        handle_key: str,
        oec_id: str,
    ) -> None:
        active = connection.execute(
            select(creator_handle_alias.c.oec_id)
            .where(
                creator_handle_alias.c.bd_market == market,
                creator_handle_alias.c.handle_key == handle_key,
                creator_handle_alias.c.valid_to.is_(None),
            )
            .with_for_update()
        ).mappings().first()
        if active and str(active["oec_id"]) != oec_id:
            raise CreatorIdentityConflict(
                f"handle {handle_key!r} 已绑定另一 OEC，拒绝改写身份"
            )

    @staticmethod
    def _insert_alias(
        connection,
        snapshot_id: str,
        market: str,
        oec_id: str,
        handle_key: str,
        handle: str,
        captured_at: datetime,
        *,
        valid_to: datetime | None = None,
    ) -> None:
        try:
            connection.execute(insert(creator_handle_alias).values(
                alias_id=uuid4().hex,
                bd_market=market,
                oec_id=oec_id,
                handle_key=handle_key,
                handle=handle,
                valid_from=captured_at,
                valid_to=valid_to,
                confirmed_snapshot_id=snapshot_id,
            ))
        except IntegrityError as exc:
            raise CreatorIdentityConflict(
                f"handle {handle_key!r} 的 active alias 发生并发冲突"
            ) from exc

    @staticmethod
    def _refresh_current(
        connection,
        snapshot_id,
        handle_key,
        profile,
        context,
        assessment,
        *,
        advance_handle: bool,
        identity_handle_key: str,
    ) -> dict:
        row = connection.execute(
            select(creator_profile_current)
            .where(
                creator_profile_current.c.bd_market == context.market,
                creator_profile_current.c.oec_id == str(profile.oec_id),
            )
            .with_for_update()
        ).mappings().first()
        valid_fields = assessment.valid_fields & _CURRENT_PROFILE_FIELDS
        incoming = {
            field_name: getattr(profile, field_name)
            for field_name in valid_fields
        }

        if row:
            field_sources = dict(row.get("field_sources") or {})
            existing_values = {
                field_name: row.get(field_name)
                for field_name in _CURRENT_PROFILE_FIELDS
                if row.get(field_name) is not None or field_name in field_sources
            }
        else:
            field_sources = {}
            existing_values = {}
        merged, merged_sources = merge_current_values(
            existing_values,
            field_sources,
            incoming,
            valid_fields,
            snapshot_id,
            context.captured_at,
        )

        if not row:
            peak_gmv_value, peak_gmv_at = next_peak_gmv(
                None,
                None,
                incoming.get("gmv_value") if "gmv_value" in valid_fields else None,
                context.captured_at,
            )
            projected_handle_key = (
                handle_key if advance_handle else identity_handle_key
            )
            current = {
                "bd_market": context.market,
                "oec_id": str(profile.oec_id),
                "handle_key": projected_handle_key,
                "handle": (
                    str(profile.handle or handle_key)
                    if advance_handle
                    else projected_handle_key
                ),
                "peak_gmv_value": peak_gmv_value,
                "peak_gmv_at": peak_gmv_at,
                **merged,
                "field_sources": merged_sources,
                "captured_at": context.captured_at,
            }
            connection.execute(insert(creator_profile_current).values(**current))
            return current

        current_captured_at = row["captured_at"]
        handle_is_current = (
            advance_handle and context.captured_at >= current_captured_at
        )
        previous_peak = row.get("peak_gmv_value")
        previous_peak_at = row.get("peak_gmv_at")
        if previous_peak is None and row.get("gmv_value") is not None:
            previous_peak = row.get("gmv_value")
            previous_peak_at = row.get("captured_at")
        peak_gmv_value, peak_gmv_at = next_peak_gmv(
            previous_peak,
            previous_peak_at,
            incoming.get("gmv_value") if "gmv_value" in valid_fields else None,
            context.captured_at,
        )
        current = dict(row)
        current.update(merged)
        current.update({
            "handle_key": handle_key if handle_is_current else row["handle_key"],
            "handle": (
                str(profile.handle or handle_key)
                if handle_is_current
                else row["handle"]
            ),
            "field_sources": merged_sources,
            "captured_at": max(context.captured_at, current_captured_at),
            "peak_gmv_value": peak_gmv_value,
            "peak_gmv_at": peak_gmv_at,
        })
        update_values = {
            field_name: current[field_name]
            for field_name in merged
        }
        update_values.update({
            "handle_key": current["handle_key"],
            "handle": current["handle"],
            "field_sources": current["field_sources"],
            "captured_at": current["captured_at"],
            "peak_gmv_value": current["peak_gmv_value"],
            "peak_gmv_at": current["peak_gmv_at"],
        })
        connection.execute(
            update(creator_profile_current)
            .where(
                creator_profile_current.c.bd_market == context.market,
                creator_profile_current.c.oec_id == str(profile.oec_id),
            )
            .values(**update_values)
        )
        return current

    @staticmethod
    def _refresh_legacy(connection, current: dict, context: CaptureContext) -> None:
        table = creator_profile
        sourced_fields = set(current.get("field_sources") or {})
        values = {
            "handle_key": current["handle_key"],
            "handle": current["handle"],
            "oec_id": current["oec_id"],
            "captured_at": current["captured_at"],
            "raw_json": {},
        }
        values.update({
            field_name: current.get(field_name)
            for field_name in sourced_fields
            if field_name in table.c
        })
        values["bd_market"] = context.market
        index_elements = ["handle_key", "bd_market"]

        statement = pg_insert(table).values(**values)
        update_fields = {
            field_name: statement.excluded[field_name]
            for field_name in values
            if field_name not in {*index_elements, "raw_json"}
        }
        connection.execute(statement.on_conflict_do_update(
            index_elements=index_elements,
            set_=update_fields,
        ))


__all__ = [
    "CreatorIdentityConflict",
    "IngestResult",
    "ProfileSnapshotStore",
    "merge_current_values",
]
