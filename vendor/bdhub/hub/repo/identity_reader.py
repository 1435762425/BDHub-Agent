# -*- coding: utf-8 -*-
"""以 OEC 为真相的达人身份只读解析。

旧 ``creator_profile`` 仍是兼容镜像，不能再作为 handle 改名后的身份判定依据。
本模块只读取 current 投影与 alias 历史；投影尚未回填或 alias 出现二义时宁可明确未解析，
绝不猜测或回退到 legacy 表的任意一行。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Iterable, Mapping

from sqlalchemy import select

from ..keys import norm_handle
from ..schema import creator_handle_alias, creator_profile_current
from ..sql_values import text_array_member


class ResolutionKind(StrEnum):
    """handle 解析结果的可信度。"""

    CURRENT = "current"
    ALIAS = "alias"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class ResolvedCreator:
    """输入 handle 所归属的稳定 OEC 与当前展示名称。"""

    oec_id: str
    handle_key: str
    handle: str
    gmv_value: Decimal | float | None = None


@dataclass(frozen=True, slots=True)
class HandleResolution:
    """单个归一化输入 handle 的解析结果。"""

    requested_handle_key: str
    kind: ResolutionKind
    creator: ResolvedCreator | None = None
    candidate_oec_ids: tuple[str, ...] = ()


def _normal_keys(handles: Iterable[str]) -> tuple[str, ...]:
    """归一、去空、按首次输入顺序去重。"""
    return tuple(dict.fromkeys(
        key for raw in handles if (key := norm_handle(str(raw)))
    ))


def _creator_from_row(row: Mapping[str, object]) -> ResolvedCreator | None:
    oec_id = str(row.get("oec_id") or "").strip()
    handle_key = norm_handle(str(row.get("current_handle_key") or ""))
    if not oec_id or oec_id == "0" or not handle_key:
        return None
    handle = str(row.get("current_handle") or "").strip() or handle_key
    return ResolvedCreator(
        oec_id=oec_id,
        handle_key=handle_key,
        handle=handle,
        gmv_value=row.get("gmv_value"),
    )


def _candidates_by_input(rows: Iterable[Mapping[str, object]]) -> dict[str, dict[str, ResolvedCreator]]:
    grouped: dict[str, dict[str, ResolvedCreator]] = {}
    for row in rows:
        requested = norm_handle(str(row.get("requested_handle_key") or ""))
        creator = _creator_from_row(row)
        if requested and creator:
            grouped.setdefault(requested, {})[creator.oec_id] = creator
    return grouped


def resolve_handle_candidates(
    requested_handles: Iterable[str],
    *,
    current_rows: Iterable[Mapping[str, object]],
    alias_rows: Iterable[Mapping[str, object]],
) -> tuple[HandleResolution, ...]:
    """按“当前 handle 优先、唯一历史 alias 兜底”归并数据库候选行。

    该纯函数既便于离线测试，也确保调用方无论查询如何拆分都使用完全一致的冲突语义。
    """
    current = _candidates_by_input(current_rows)
    aliases = _candidates_by_input(alias_rows)
    resolved: list[HandleResolution] = []
    for handle_key in _normal_keys(requested_handles):
        candidates = current.get(handle_key, {})
        if len(candidates) == 1:
            resolved.append(HandleResolution(
                requested_handle_key=handle_key,
                kind=ResolutionKind.CURRENT,
                creator=next(iter(candidates.values())),
            ))
            continue
        if len(candidates) > 1:
            resolved.append(HandleResolution(
                requested_handle_key=handle_key,
                kind=ResolutionKind.AMBIGUOUS,
                candidate_oec_ids=tuple(sorted(candidates)),
            ))
            continue

        candidates = aliases.get(handle_key, {})
        if len(candidates) == 1:
            resolved.append(HandleResolution(
                requested_handle_key=handle_key,
                kind=ResolutionKind.ALIAS,
                creator=next(iter(candidates.values())),
            ))
        elif len(candidates) > 1:
            resolved.append(HandleResolution(
                requested_handle_key=handle_key,
                kind=ResolutionKind.AMBIGUOUS,
                candidate_oec_ids=tuple(sorted(candidates)),
            ))
        else:
            resolved.append(HandleResolution(
                requested_handle_key=handle_key,
                kind=ResolutionKind.MISSING,
            ))
    return tuple(resolved)


class CreatorIdentityReader:
    """批量读取 OEC 当前投影，并把旧 handle 解析为当前身份。"""

    def __init__(self, engine):
        self.engine = engine

    def resolve_handles(
        self,
        market: str,
        handles: Iterable[str],
    ) -> tuple[HandleResolution, ...]:
        handle_keys = _normal_keys(handles)
        if not handle_keys:
            return ()
        normalized_market = str(market or "").strip().lower()
        if not normalized_market:
            raise ValueError("market 不能为空")

        current_query = select(
            creator_profile_current.c.handle_key.label("requested_handle_key"),
            creator_profile_current.c.oec_id,
            creator_profile_current.c.handle_key.label("current_handle_key"),
            creator_profile_current.c.handle.label("current_handle"),
            creator_profile_current.c.gmv_value,
        ).where(
            creator_profile_current.c.bd_market == normalized_market,
            text_array_member(
                creator_profile_current.c.handle_key,
                handle_keys,
                parameter="current_handle_keys",
            ),
        )
        alias_query = select(
            creator_handle_alias.c.handle_key.label("requested_handle_key"),
            creator_profile_current.c.oec_id,
            creator_profile_current.c.handle_key.label("current_handle_key"),
            creator_profile_current.c.handle.label("current_handle"),
            creator_profile_current.c.gmv_value,
        ).select_from(
            creator_handle_alias.join(
                creator_profile_current,
                (creator_profile_current.c.bd_market == creator_handle_alias.c.bd_market)
                & (creator_profile_current.c.oec_id == creator_handle_alias.c.oec_id),
            )
        ).where(
            creator_handle_alias.c.bd_market == normalized_market,
            text_array_member(
                creator_handle_alias.c.handle_key,
                handle_keys,
                parameter="alias_handle_keys",
            ),
        )
        with self.engine.connect() as connection:
            current_rows = connection.execute(current_query).mappings().all()
            alias_rows = connection.execute(alias_query).mappings().all()
        return resolve_handle_candidates(
            handle_keys,
            current_rows=current_rows,
            alias_rows=alias_rows,
        )


__all__ = [
    "CreatorIdentityReader",
    "HandleResolution",
    "ResolutionKind",
    "ResolvedCreator",
    "resolve_handle_candidates",
]
