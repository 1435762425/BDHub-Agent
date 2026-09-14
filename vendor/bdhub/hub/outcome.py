from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

T = TypeVar("T")


class OutcomeKind(StrEnum):
    OK = "ok"
    EXACT_MISS = "exact_miss"
    AMBIGUOUS_EMPTY = "ambiguous_empty"
    AUTH_EXPIRED = "auth_expired"
    THROTTLED = "throttled"
    PROTOCOL_ERROR = "protocol_error"
    NETWORK_ERROR = "network_error"
    PROFILE_WALL = "profile_wall"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True, slots=True)
class Outcome(Generic[T]):
    kind: OutcomeKind
    value: T | None = None
    subject: str | None = None
    retryable: bool = False
    remote_code: str | None = None
    detail: str | None = None

    @classmethod
    def success(cls, value: T, *, subject: str | None = None) -> "Outcome[T]":
        return cls(kind=OutcomeKind.OK, value=value, subject=subject)

    @classmethod
    def failure(
        cls,
        kind: OutcomeKind,
        *,
        subject: str | None = None,
        retryable: bool,
        remote_code: str | None = None,
        detail: str | None = None,
    ) -> "Outcome[T]":
        if kind is OutcomeKind.OK:
            raise ValueError("failure() 不能使用 OutcomeKind.OK")
        return cls(kind=kind, subject=subject, retryable=retryable,
                   remote_code=remote_code, detail=detail)


@dataclass(frozen=True, slots=True)
class ExactMissEvidence:
    source: str
    detail: str
    remote_code: str | None = None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("exact miss evidence source 不能为空")
        if not self.detail.strip():
            raise ValueError("exact miss evidence detail 不能为空")
