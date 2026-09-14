# -*- coding: utf-8 -*-
"""把前端业务动作解析为后端抓取步骤。

前端只表达“刷新什么”，不直接提交 Profile 分型。分型策略与传输层彼此
独立，便于用同一批目标做浏览器/纯 HTTP 和拆分/组合请求的正交 A/B。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RefreshAction(StrEnum):
    """各前端入口最终收敛到的业务动作。"""

    L1 = "l1"
    L1_PROFILE2 = "l1_profile2"
    PROFILE2 = "profile2"
    IDENTITY_PROFILE2 = "identity_profile2"


class RefreshEndpoint(StrEnum):
    FIND = "find"
    PROFILE = "profile"


class RefreshTransport(StrEnum):
    BROWSER = "browser"
    PURE_HTTP = "pure_http"
    PURE_HTTP_EXPERIMENT = "pure_http_experiment"


class ProfileStrategy(StrEnum):
    """Profile 请求形状；选择依据是门禁数据，不是传输层类型。"""

    SPLIT_IDENTITY_METRICS = "split_identity_metrics"
    COMBINED_126 = "combined_126"


@dataclass(frozen=True, slots=True)
class RefreshStep:
    endpoint: RefreshEndpoint
    profile_types: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.endpoint is RefreshEndpoint.FIND:
            if self.profile_types:
                raise ValueError("Find 步骤不能携带 Profile 分型")
            return
        if (
            not self.profile_types
            or any(type(value) is not int or value <= 0 for value in self.profile_types)
            or len(set(self.profile_types)) != len(self.profile_types)
        ):
            raise ValueError("Profile 步骤必须携带唯一的正整数分型")


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    action: RefreshAction
    transport: RefreshTransport
    profile_strategy: ProfileStrategy
    steps: tuple[RefreshStep, ...]


def _profile_steps(
    action: RefreshAction,
    strategy: ProfileStrategy,
) -> tuple[RefreshStep, ...]:
    if action is RefreshAction.L1:
        return ()
    if strategy is ProfileStrategy.COMBINED_126:
        return (RefreshStep(RefreshEndpoint.PROFILE, (1, 2, 6)),)
    if action is RefreshAction.IDENTITY_PROFILE2:
        return (
            RefreshStep(RefreshEndpoint.PROFILE, (1, 6)),
            RefreshStep(RefreshEndpoint.PROFILE, (2,)),
        )
    return (RefreshStep(RefreshEndpoint.PROFILE, (2,)),)


def build_refresh_plan(
    action: RefreshAction | str,
    *,
    has_handle: bool,
    has_oec: bool,
    transport: RefreshTransport | str = RefreshTransport.BROWSER,
    profile_strategy: ProfileStrategy | str = (
        ProfileStrategy.SPLIT_IDENTITY_METRICS
    ),
    experiment_enabled: bool = False,
) -> RefreshPlan:
    """按身份条件和动态策略生成不可变步骤。

    ``experiment_enabled`` 只控制纯 HTTP 传输是否可选，不限制组合分型；
    `[1,2,6]` 可以在任一传输层经过 A/B 门禁后启用。
    """
    try:
        selected_action = RefreshAction(action)
        selected_transport = RefreshTransport(transport)
        selected_strategy = ProfileStrategy(profile_strategy)
    except (TypeError, ValueError) as exc:
        raise ValueError("抓取计划参数无效") from exc
    if type(has_handle) is not bool or type(has_oec) is not bool:
        raise ValueError("身份条件必须是布尔值")
    if type(experiment_enabled) is not bool:
        raise ValueError("实验开关必须是布尔值")
    if (
        selected_transport is RefreshTransport.PURE_HTTP_EXPERIMENT
        and not experiment_enabled
    ):
        raise ValueError("纯 HTTP 实验传输层未启用")

    needs_handle = selected_action in {
        RefreshAction.L1,
        RefreshAction.L1_PROFILE2,
        RefreshAction.PROFILE2,
    }
    needs_oec = selected_action in {
        RefreshAction.PROFILE2,
        RefreshAction.IDENTITY_PROFILE2,
    }
    if (needs_handle and not has_handle) or (needs_oec and not has_oec):
        raise ValueError("抓取计划身份条件不足")

    find_steps = (
        (RefreshStep(RefreshEndpoint.FIND),)
        if selected_action in {RefreshAction.L1, RefreshAction.L1_PROFILE2}
        else ()
    )
    return RefreshPlan(
        action=selected_action,
        transport=selected_transport,
        profile_strategy=selected_strategy,
        steps=find_steps + _profile_steps(selected_action, selected_strategy),
    )


__all__ = [
    "ProfileStrategy",
    "RefreshAction",
    "RefreshEndpoint",
    "RefreshPlan",
    "RefreshStep",
    "RefreshTransport",
    "build_refresh_plan",
]
