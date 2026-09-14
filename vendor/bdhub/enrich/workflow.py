# -*- coding: utf-8 -*-
"""Light 富化工作项的纯状态转移策略。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bdhub.hub.outcome import Outcome, OutcomeKind


class ItemState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    UNRESOLVED = "unresolved"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RetryBudget:
    total_attempts: int = 0
    error_retries: int = 0
    persistence_retries: int = 0
    max_total_attempts: int = 8
    max_error_retries: int = 3
    max_persistence_retries: int = 2

    def __post_init__(self) -> None:
        counters = (
            self.total_attempts,
            self.error_retries,
            self.persistence_retries,
        )
        limits = (
            self.max_total_attempts,
            self.max_error_retries,
            self.max_persistence_retries,
        )
        if any(value < 0 for value in counters):
            raise ValueError("重试计数不能为负数")
        if any(value <= 0 for value in limits):
            raise ValueError("重试上限必须大于 0")


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    state: ItemState
    reason: str
    consume_error_retry: bool = False
    consume_persistence_retry: bool = False
    account_cooldown: bool = False
    requires_ingestion: bool = False


_TRAFFIC_WALLS = {OutcomeKind.THROTTLED, OutcomeKind.PROFILE_WALL}
_BOUNDED_ERRORS = {
    OutcomeKind.NETWORK_ERROR,
    OutcomeKind.PROTOCOL_ERROR,
    OutcomeKind.AMBIGUOUS_EMPTY,
}
_TERMINAL_REQUEST_FAILURES = {
    OutcomeKind.EXACT_MISS,
    OutcomeKind.INVALID_INPUT,
}


def decide_transition(
    outcome: Outcome,
    budget: RetryBudget,
    *,
    failure_class: str = "request",
) -> TransitionDecision:
    """根据已持久计数决定下一状态，不执行 I/O 或 sleep。"""
    if not isinstance(outcome, Outcome):
        raise TypeError("outcome 必须是 Outcome")
    if not isinstance(budget, RetryBudget):
        raise TypeError("budget 必须是 RetryBudget")
    if failure_class not in {"request", "persistence"}:
        raise ValueError("failure_class 只支持 request/persistence")

    if outcome.kind is OutcomeKind.OK:
        if failure_class != "request":
            raise ValueError("成功 Outcome 不能标记为持久化失败")
        return TransitionDecision(
            state=ItemState.RUNNING,
            reason="awaiting_ingestion",
            requires_ingestion=True,
        )

    if failure_class == "persistence":
        if (
            budget.total_attempts >= budget.max_total_attempts
            or budget.persistence_retries >= budget.max_persistence_retries
        ):
            return TransitionDecision(
                state=ItemState.DEAD_LETTER,
                reason="persistence_exhausted",
            )
        return TransitionDecision(
            state=ItemState.RETRY_WAIT,
            reason="persistence_retry",
            consume_persistence_retry=True,
        )

    account_cooldown = outcome.kind in _TRAFFIC_WALLS
    if budget.total_attempts >= budget.max_total_attempts:
        return TransitionDecision(
            state=ItemState.UNRESOLVED,
            reason="total_attempts_exhausted",
            account_cooldown=account_cooldown,
        )

    if outcome.kind in _TRAFFIC_WALLS:
        return TransitionDecision(
            state=ItemState.RETRY_WAIT,
            reason=outcome.kind.value,
            account_cooldown=True,
        )

    if outcome.kind is OutcomeKind.AUTH_EXPIRED:
        return TransitionDecision(
            state=ItemState.RETRY_WAIT,
            reason=OutcomeKind.AUTH_EXPIRED.value,
        )

    if outcome.kind in _TERMINAL_REQUEST_FAILURES:
        return TransitionDecision(
            state=ItemState.UNRESOLVED,
            reason=outcome.kind.value,
        )

    if outcome.kind in _BOUNDED_ERRORS:
        if (
            not outcome.retryable
            or budget.error_retries >= budget.max_error_retries
        ):
            return TransitionDecision(
                state=ItemState.UNRESOLVED,
                reason=f"{outcome.kind.value}_exhausted",
            )
        return TransitionDecision(
            state=ItemState.RETRY_WAIT,
            reason=outcome.kind.value,
            consume_error_retry=True,
        )

    raise ValueError(f"未支持的 OutcomeKind: {outcome.kind!r}")
