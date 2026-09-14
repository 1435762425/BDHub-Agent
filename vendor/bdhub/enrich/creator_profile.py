# -*- coding: utf-8 -*-
"""按 handle 获取达人画像。

默认业务深度固定为 L1 Find + Profile [2]。Profile [1,6]/[3]/[4] 只保留为显式扩展能力，
不得由默认入口调用。Profile 必须按分型单独请求，合并请求会缩水。
"""
from __future__ import annotations

from dataclasses import replace
from enum import StrEnum

from ..hub.models import parse_find_item, CreatorProfile
from ..hub.keys import norm_handle  # re-export,兼容旧引用
from ..hub.outcome import Outcome, OutcomeKind
from ..hub.profile_observation import required_profile_fields
from .client import RateLimitError, AuthError
from .profile_transport import BrowserProfileResult
from .refresh_plan import (
    ProfileStrategy,
    RefreshAction,
    RefreshEndpoint,
    build_refresh_plan,
)

# 默认业务深度：L1 Find + 销售表现 Profile [2]。
# 其余分型只保留为显式扩展能力，不得进入默认抓取链路。
DEFAULT_PROFILE_TYPE_SETS = ((2,),)
PROFILE_TYPE_SETS = [[1, 6], [2], [3], [4]]


def _planned_profile_type_sets(
    action: RefreshAction,
    strategy: ProfileStrategy | str,
) -> tuple[tuple[int, ...], ...]:
    plan = build_refresh_plan(
        action,
        has_handle=action is not RefreshAction.IDENTITY_PROFILE2,
        has_oec=action in {
            RefreshAction.PROFILE2,
            RefreshAction.IDENTITY_PROFILE2,
        },
        profile_strategy=strategy,
    )
    return tuple(
        step.profile_types
        for step in plan.steps
        if step.endpoint is RefreshEndpoint.PROFILE
    )


class OecLightRoute(StrEnum):
    """OEC Light 候选名；生产白名单由 ``select_oec_route`` 收紧。"""

    HTTP_CONTROL_16_2 = "http_control_16_2"
    BROWSER_16 = "browser_16"
    BROWSER_2 = "browser_2"
    BROWSER_162 = "browser_162"
    MCN_RELATION_BROWSER_2 = "mcn_relation_browser_2"
    MCN_RELATION_BROWSER_126 = "mcn_relation_browser_126"
    KNOWN_OEC_BROWSER_2 = "known_oec_browser_2"
    RESOLVED_OEC_BROWSER_2 = "resolved_oec_browser_2"
    KNOWN_OEC_BROWSER_126 = "known_oec_browser_126"
    RESOLVED_OEC_BROWSER_126 = "resolved_oec_browser_126"


_PRODUCTION_OEC_LIGHT_ROUTES = frozenset({
    OecLightRoute.HTTP_CONTROL_16_2,
    OecLightRoute.BROWSER_16,
    OecLightRoute.MCN_RELATION_BROWSER_2,
    OecLightRoute.MCN_RELATION_BROWSER_126,
    OecLightRoute.KNOWN_OEC_BROWSER_2,
    OecLightRoute.RESOLVED_OEC_BROWSER_2,
    OecLightRoute.KNOWN_OEC_BROWSER_126,
    OecLightRoute.RESOLVED_OEC_BROWSER_126,
})


def select_oec_route(value: str | OecLightRoute | None) -> OecLightRoute:
    """只允许旧 HTTP 控制路线和通过固定 50 人门禁的 browser_16。"""
    if value is None:
        return OecLightRoute.BROWSER_16
    if value == "oec_http":
        return OecLightRoute.HTTP_CONTROL_16_2
    try:
        route = OecLightRoute(value)
    except (TypeError, ValueError):
        raise ValueError(f"OEC Light 路由未通过生产门禁: {value!r}") from None
    if route not in _PRODUCTION_OEC_LIGHT_ROUTES:
        raise ValueError(f"OEC Light 路由未通过生产门禁: {route.value!r}")
    return route


def fetch_light_by_oec_browser(transport, oec: str) -> Outcome:
    """用已签名浏览器发一次 ``[1,6]``，并在解析前强制核对 OEC 身份。"""
    outcome = transport.fetch(oec, [1, 6])
    if not isinstance(outcome, Outcome):
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=True,
            detail="browser_profile_invalid_outcome",
        )
    if outcome.kind is not OutcomeKind.OK:
        return outcome
    result = outcome.value
    if not isinstance(result, BrowserProfileResult):
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=True,
            detail="browser_profile_invalid_success",
        )
    try:
        profile = parse_find_item(result.profile)
    except Exception:
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=True,
            detail="browser_profile_parse_error",
        )
    if not profile.handle or profile.oec_id != oec:
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=True,
            detail="browser_profile_identity_mismatch",
        )
    return Outcome.success(profile, subject=oec)


def fetch_profile2_by_known_oec_browser(
    transport,
    oec: str,
    *,
    market: str = "mx",
    profile_strategy: ProfileStrategy | str = (
        ProfileStrategy.SPLIT_IDENTITY_METRICS
    ),
) -> Outcome:
    """已知 OEC 的身份补全；请求形状由统一 Profile 策略决定。"""
    if profile_strategy == ProfileStrategy.COMBINED_126:
        return fetch_complete_oec_browser(transport, oec, market=market)
    profile_type_sets = _planned_profile_type_sets(
        RefreshAction.IDENTITY_PROFILE2,
        profile_strategy,
    )
    identity_outcome = transport.fetch(oec, list(profile_type_sets[0]))
    if not isinstance(identity_outcome, Outcome):
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=True,
            detail="known_oec_profile2_invalid_outcome",
        )
    if identity_outcome.kind is not OutcomeKind.OK:
        return identity_outcome
    identity_result = identity_outcome.value
    if not isinstance(identity_result, BrowserProfileResult):
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=True,
            detail="known_oec_profile2_invalid_success",
        )
    try:
        identity = parse_find_item(identity_result.profile)
    except Exception:
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=False,
            detail="known_oec_profile2_missing_identity",
        )
    if not identity.handle:
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=False,
            detail="known_oec_profile2_missing_identity",
        )
    if identity.oec_id != oec:
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=False,
            detail="known_oec_profile2_identity_mismatch",
        )

    if len(profile_type_sets) == 1:
        return Outcome.success(identity, subject=oec)

    metrics_outcome = transport.fetch(oec, list(profile_type_sets[1]))
    if not isinstance(metrics_outcome, Outcome):
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=True,
            detail="known_oec_profile2_invalid_outcome",
        )
    if metrics_outcome.kind is not OutcomeKind.OK:
        return metrics_outcome
    metrics_result = metrics_outcome.value
    if not isinstance(metrics_result, BrowserProfileResult):
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=True,
            detail="known_oec_profile2_invalid_success",
        )
    try:
        profile = parse_find_item(merge_profiles([
            identity_result.profile,
            metrics_result.profile,
        ]))
    except Exception:
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=True,
            detail="known_oec_profile2_parse_error",
        )
    if profile.handle != identity.handle or profile.oec_id != oec:
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=False,
            detail="known_oec_profile2_identity_mismatch",
        )
    return Outcome.success(profile, subject=oec)


def fetch_profile2_by_resolved_oec_browser(
    transport,
    oec: str,
    handle: str,
    *,
    market: str = "mx",
    profile_strategy: ProfileStrategy | str = (
        ProfileStrategy.SPLIT_IDENTITY_METRICS
    ),
) -> Outcome:
    """身份已由本地 current/alias 解析时，只请求 Profile [2] 并回填已知身份。"""
    known_handle = norm_handle(handle)
    if not known_handle:
        return Outcome.failure(
            OutcomeKind.INVALID_INPUT,
            subject=oec,
            retryable=False,
            detail="resolved_oec_profile2_missing_handle",
        )
    if profile_strategy == ProfileStrategy.COMBINED_126:
        # OEC是身份真值；本地旧handle只作审计，不拒绝同OEC的真实改名。
        return fetch_complete_oec_browser(transport, oec, market=market)
    profile_type_sets = _planned_profile_type_sets(
        RefreshAction.PROFILE2,
        profile_strategy,
    )
    outcome = transport.fetch(oec, list(profile_type_sets[0]))
    if not isinstance(outcome, Outcome):
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=True,
            detail="resolved_oec_profile2_invalid_outcome",
        )
    if outcome.kind is not OutcomeKind.OK:
        return outcome
    result = outcome.value
    if not isinstance(result, BrowserProfileResult):
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=True,
            detail="resolved_oec_profile2_invalid_success",
        )
    try:
        parsed = parse_find_item(result.profile)
    except Exception:
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=True,
            detail="resolved_oec_profile2_parse_error",
        )
    if parsed.oec_id and parsed.oec_id != oec:
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=False,
            detail="resolved_oec_profile2_identity_mismatch",
        )
    if parsed.handle and norm_handle(parsed.handle) != known_handle:
        return Outcome.failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=oec,
            retryable=False,
            detail="resolved_oec_profile2_identity_mismatch",
        )
    return Outcome.success(
        replace(parsed, handle=known_handle, oec_id=oec),
        subject=oec,
    )


def pick_exact(find_json: dict, handle: str) -> dict | None:
    """只返回与输入 handle 精确匹配的那条；没有就 None(记 miss)。
    绝不回退取第一条——否则死号会被映射成不相关的热门达人，污染库(重复)。"""
    lst = find_json.get("creator_profile_list") or []
    want = norm_handle(handle)
    for it in lst:
        hv = it.get("handle")
        hval = hv.get("value") if isinstance(hv, dict) else hv
        if norm_handle(str(hval or "")) == want:
            return it
    return None


def _has_real(f) -> bool:
    """字段是否带真值（区分"有壳无值"的未授权/无数据字段）。"""
    if not isinstance(f, dict):
        return f not in (None, "", [], {})
    if "value" not in f:
        return False
    return f["value"] not in (None, "", [], {})


def merge_profiles(parts: list[dict]) -> dict:
    """合并 find 项 + 各分型 profile：有真值的覆盖，无真值的仅占位。"""
    out: dict = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        for k, v in part.items():
            if _has_real(v) or k not in out:
                out[k] = v
    return out


def fetch_complete_oec_browser(transport, oec: str, *, market: str = "mx") -> Outcome:
    """组合画像优先；缺字段才同账号补抓，每个分型最多一次。"""
    parts: list[dict] = []
    required = required_profile_fields(market)

    def fetch(types):
        outcome = transport.fetch(oec, list(types))
        if not isinstance(outcome, Outcome):
            return Outcome.failure(OutcomeKind.PROTOCOL_ERROR, subject=oec,
                                   retryable=True, detail="browser_profile_invalid_outcome")
        if outcome.kind is not OutcomeKind.OK:
            return outcome
        value = outcome.value
        if not isinstance(value, BrowserProfileResult):
            return Outcome.failure(OutcomeKind.PROTOCOL_ERROR, subject=oec,
                                   retryable=True, detail="browser_profile_invalid_success")
        if _oec_of(value.profile) != oec:
            return Outcome.failure(OutcomeKind.PROTOCOL_ERROR, subject=oec,
                                   retryable=False, detail="browser_profile_identity_mismatch")
        parts.append(value.profile)
        try:
            return Outcome.success(parse_find_item(merge_profiles(parts)), subject=oec)
        except Exception:
            return Outcome.failure(OutcomeKind.PROTOCOL_ERROR, subject=oec,
                                   retryable=True, detail="browser_profile_parse_error")

    result = fetch((1, 2, 6))
    if result.kind is not OutcomeKind.OK:
        return result
    if not result.value.handle or result.value.followers is None:
        result = fetch((1, 6))
        if result.kind is not OutcomeKind.OK:
            return result
    if "gmv_value" in required and result.value.gmv_value is None:
        result = fetch((2,))
        if result.kind is not OutcomeKind.OK:
            return result
    if any(getattr(result.value, field, None) in (None, "") for field in required):
        return Outcome.failure(OutcomeKind.PROTOCOL_ERROR, subject=oec, retryable=True,
                               detail="profile_required_fields_missing")
    return result


def _oec_of(item: dict) -> str:
    v = item.get("creator_oecuid")
    v = v.get("value") if isinstance(v, dict) else v
    return str(v or "")


def _fetch_from_find(
    client,
    handle: str,
    profile_type_sets,
) -> CreatorProfile | None:
    item = pick_exact(client.find(handle, query_type=1), handle)
    if not item:
        return None
    oec = _oec_of(item)
    parts = [item]
    if oec:
        for profile_types in profile_type_sets:
            types = list(profile_types)
            try:
                cp = client.profile(oec, types).get("creator_profile") or {}
            except (RateLimitError, AuthError):
                raise
            except Exception:
                cp = {}
            if cp:
                parts.append(cp)
    return parse_find_item(merge_profiles(parts))


def fetch_full(
    client,
    handle: str,
    delay: float = 0.6,
    *,
    profile_strategy: ProfileStrategy | str = (
        ProfileStrategy.SPLIT_IDENTITY_METRICS
    ),
) -> CreatorProfile | None:
    """L1 Find 后按当前门禁策略抓 Profile；默认仍为 `[2]`。"""
    return _fetch_from_find(
        client,
        handle,
        _planned_profile_type_sets(RefreshAction.L1_PROFILE2, profile_strategy),
    )


def fetch_extended(client, handle: str) -> CreatorProfile | None:
    """显式扩展深抓；仅在业务明确需要受众、bio 等字段时调用。"""
    return _fetch_from_find(client, handle, PROFILE_TYPE_SETS)


def fetch_light_by_oec(
    client,
    oec: str,
    *,
    profile_strategy: ProfileStrategy | str = (
        ProfileStrategy.SPLIT_IDENTITY_METRICS
    ),
) -> CreatorProfile | None:
    """按 oec 轻量刷新(2 调用,2026-07-20 实测定型):[1,6]=身份/粉丝/类目,[2]=全部 GMV 字段
    (gmv/video/live/units/gpm 实测都在 type2)。刷新快照够用;要品牌/bio 等全量仍用 fetch_full_by_oec。"""
    parts = []
    for profile_types in _planned_profile_type_sets(
        RefreshAction.IDENTITY_PROFILE2,
        profile_strategy,
    ):
        types = list(profile_types)
        try:
            cp = client.profile(oec, types).get("creator_profile") or {}
        except (RateLimitError, AuthError):
            raise
        except Exception:
            cp = {}
        if cp:
            parts.append(cp)
    if not parts:
        return None
    return parse_find_item(merge_profiles(parts))


def fetch_light_by_oec_strict(
    client,
    oec: str,
    *,
    profile_strategy: ProfileStrategy | str = (
        ProfileStrategy.SPLIT_IDENTITY_METRICS
    ),
) -> CreatorProfile | None:
    """OEC 可恢复任务专用：两次请求任一失败都向上抛，不伪装成局部成功。"""
    parts = []
    for profile_types in _planned_profile_type_sets(
        RefreshAction.IDENTITY_PROFILE2,
        profile_strategy,
    ):
        response = client.profile(oec, list(profile_types))
        if not isinstance(response, dict):
            raise RuntimeError("profile 响应结构无效")
        profile = response.get("creator_profile") or {}
        if not isinstance(profile, dict):
            raise RuntimeError("profile 响应结构无效")
        if profile:
            parts.append(profile)
    if not parts:
        return None
    return parse_find_item(merge_profiles(parts))


def fetch_full_by_oec(client, oec: str) -> CreatorProfile | None:
    """按 oec_id 直接富化(绕开 find)。给【IM 会话里有 oec 但没富化 / MCN find 搜不到】的达人补全身份。
    分型 profile(oec,[1,6]/[2]/[3]/[4]) 各调一次合并解析;无返回则 None。限流/失效向上抛(调用方退避)。"""
    parts = []
    for types in PROFILE_TYPE_SETS:
        try:
            cp = client.profile(oec, types).get("creator_profile") or {}
        except (RateLimitError, AuthError):
            raise
        except Exception:
            cp = {}
        if cp:
            parts.append(cp)
    if not parts:
        return None
    return parse_find_item(merge_profiles(parts))


def fetch_light(client, handle: str) -> CreatorProfile | None:
    """只调 find 的轻量画像（快，省额度；字段不全）。"""
    return parse_item(pick_exact(client.find(handle, query_type=1), handle))


def parse_item(item: dict | None) -> CreatorProfile | None:
    return parse_find_item(item) if item else None
