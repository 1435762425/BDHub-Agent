"""账号市场、兼容用途提示与浏览器 profile 的共享绑定边界。"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from bdhub import config as cfgmod
from bdhub.account_policy import (
    has_explicit_listener_pool,
    resolve_account_policy,
)
from bdhub.hub.market_catalog import MARKET_CATALOG


def normalize_registered_market(value: object) -> str:
    """接受 Dashboard 已登记市场；具体远端能力由调用侧再做门禁。"""
    if not isinstance(value, str):
        raise ValueError("unsupported_market")
    market = value.strip().lower()
    if not market or market not in MARKET_CATALOG:
        raise ValueError("unsupported_market")
    return market


def account_market(account) -> str:
    """读取监听账号的固定市场；旧账号缺字段时兼容为 MX。"""
    raw_market = getattr(account, "market", "mx")
    try:
        return normalize_registered_market(raw_market)
    except ValueError:
        raise ValueError("im_account_market_invalid") from None


def account_im_role(account) -> str:
    """读取账号历史默认用途；旧账号缺字段时兼容为 sender。"""
    raw_role = getattr(account, "im_role", "sender")
    try:
        return cfgmod.validate_im_role(raw_role)
    except ValueError:
        raise ValueError("im_account_role_invalid") from None


def require_im_account(
    account,
    *,
    requested_market: str,
    required_role: str,
) -> str:
    """统一校验账号启用状态和监听市场边界。

    ``required_role`` 仅保留为调用契约兼容字段。所有启用账号都具备完整
    IM 权限，历史 ``im_role`` 只表达默认用途，不能作为授权门禁。只有进入
    监听池的账号固定绑定一个市场；其他账号可在已启用市场间通用。
    """
    market = normalize_registered_market(requested_market)
    cfgmod.validate_im_role(required_role)
    if getattr(account, "enabled", True) is False:
        raise ValueError("im_account_disabled")
    if (
        resolve_account_policy(account).listener_pool
        and account_market(account) != market
    ):
        raise ValueError("im_account_market_mismatch")
    return market


def select_monitor_account(accounts: Iterable, market: str):
    """显式监听池按优先级选择；全旧配置保持首个启用账号兼容。"""
    clean_market = normalize_registered_market(market)
    candidates = [
        account for account in accounts
        if getattr(account, "enabled", True) is not False
        and account_market(account) == clean_market
    ]
    if not candidates:
        raise ValueError("monitor_account_required")
    if not any(has_explicit_listener_pool(account) for account in candidates):
        return candidates[0]
    selected = []
    for account in candidates:
        bound_market = account_market(account)
        if (
            getattr(account, "enabled", True) is not False
            and bound_market == clean_market
            and resolve_account_policy(account).listener_pool
        ):
            selected.append(account)
    if not selected:
        raise ValueError("monitor_account_required")
    return min(
        selected,
        key=lambda account: (
            resolve_account_policy(account).listener_priority,
            str(account.name),
        ),
    )


def sender_accounts(accounts: Iterable, market: str) -> list:
    """返回可发送账号；仅监听池账号受其固定市场约束。"""
    clean_market = normalize_registered_market(market)
    candidates = list(accounts)
    explicit = any(
        getattr(account, "im_send_pool", None) is not None
        for account in candidates
    )
    selected = []
    for account in candidates:
        account_im_role(account)
        policy = resolve_account_policy(account)
        if policy.listener_pool and account_market(account) != clean_market:
            continue
        if (
            getattr(account, "enabled", True) is not False
            and (
                not explicit
                or policy.im_send_pool
            )
        ):
            selected.append(account)
    return selected


def _resolved_profile(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = cfgmod.ROOT / path
    return path.resolve()


def resolve_bound_profile(
    account,
    requested_profile: str | Path | None = None,
) -> Path:
    """只允许账号配置中固定的 profile；显式覆盖只能传同一路径。"""
    bound_profile = _resolved_profile(account.profile_dir)
    if requested_profile is None:
        return bound_profile
    if _resolved_profile(requested_profile) != bound_profile:
        raise ValueError("im_profile_account_mismatch")
    return bound_profile


__all__ = [
    "account_im_role",
    "account_market",
    "has_explicit_listener_pool",
    "normalize_registered_market",
    "require_im_account",
    "resolve_bound_profile",
    "select_monitor_account",
    "sender_accounts",
]
