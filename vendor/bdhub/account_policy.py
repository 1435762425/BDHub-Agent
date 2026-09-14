"""账号能力池与账号级运行参数的统一解释层。"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class AccountPolicy:
    listener_pool: bool
    listener_priority: int
    im_send_pool: bool
    collection_pool: bool
    report_pool: bool
    share_link_pool: bool
    sample_review_pool: bool
    sample_review_priority: int
    identity_lifecycle_enabled: bool
    auto_relogin: bool
    data_qps: float
    data_concurrency: int
    im_send_interval_seconds: float

    def public_dict(self) -> dict:
        return asdict(self)


def _bool(account, field: str, default: bool) -> bool:
    value = getattr(account, field, None)
    return default if value is None else bool(value)


def resolve_account_policy(account, *, default_qps: float = 1.2,
                           default_concurrency: int = 2) -> AccountPolicy:
    """把旧账号配置映射为显式池策略；不修改原配置文件。"""
    legacy_monitor = str(getattr(account, "im_role", "sender") or "").strip().lower() == "monitor"
    listener = _bool(account, "listener_pool", legacy_monitor)
    collection = _bool(account, "collection_pool", not listener)
    return AccountPolicy(
        listener_pool=listener,
        listener_priority=int(getattr(account, "listener_priority", None) or 100),
        im_send_pool=_bool(account, "im_send_pool", True),
        collection_pool=collection,
        report_pool=_bool(account, "report_pool", collection),
        share_link_pool=_bool(account, "share_link_pool", False),
        sample_review_pool=_bool(account, "sample_review_pool", False),
        sample_review_priority=int(
            getattr(account, "sample_review_priority", None) or 100
        ),
        identity_lifecycle_enabled=_bool(
            account, "identity_lifecycle_enabled", True
        ),
        auto_relogin=_bool(account, "auto_relogin", True),
        data_qps=float(getattr(account, "data_qps", None) or default_qps),
        data_concurrency=int(
            getattr(account, "data_concurrency", None) or default_concurrency
        ),
        im_send_interval_seconds=float(
            getattr(account, "im_send_interval_seconds", None) or 5.0
        ),
    )


def has_explicit_listener_pool(account) -> bool:
    return getattr(account, "listener_pool", None) is not None


__all__ = ["AccountPolicy", "has_explicit_listener_pool", "resolve_account_policy"]
