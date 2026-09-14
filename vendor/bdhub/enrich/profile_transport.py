# -*- coding: utf-8 -*-
"""浏览器内签名的 OEC profile 请求；只跨边界返回固定结构。"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ..hub.markets import find_url
from ..hub.outcome import Outcome, OutcomeKind


_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_AUTH_CODES = {"10001", "98000001", "98000002"}


def profile_url(market: str, partner_id: str) -> str:
    """复用市场 find 基础参数，只替换固定 endpoint path。"""
    url = find_url(market, partner_id)
    marker = "/4partner/find?"
    if marker not in url:
        raise ValueError("市场 find URL 结构无效")
    return url.replace(marker, "/4partner/profile?", 1)


PROFILE_FETCH_JS = r"""async ({url, oec, types, timeoutMs=20000}) => {
  const started = performance.now();
  const elapsed = () => Math.max(0, performance.now() - started);
  const deadlineMs = Number.isFinite(timeoutMs) && timeoutMs > 0
    ? Math.min(timeoutMs, 60000) : 20000;
  const controller = new AbortController();
  let timer;
  const hasHeader = (response, name) => {
    try {
      if (!response || !response.headers) return false;
      if (typeof response.headers.has === 'function') {
        return response.headers.has(name);
      }
      return typeof response.headers.get === 'function'
        && response.headers.get(name) !== null;
    } catch (_) { return false; }
  };
  const headerEquals = (response, name, expected) => {
    try {
      return Boolean(response && response.headers
        && typeof response.headers.get === 'function'
        && String(response.headers.get(name) || '').trim() === expected);
    } catch (_) { return false; }
  };
  const run = async () => { try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({creator_oec_id: String(oec), profile_types: types}),
      credentials: 'include',
      signal: controller.signal
    });
    if (hasHeader(response, 'bdturing-verify')) {
      return {kind:'verification', status:response.status, code:'10000',
              elapsed_ms:elapsed()};
    }
    const systemError3 = headerEquals(response, 'x-tt-system-error', '3');
    if (!response.ok) {
      return {kind:'error', status:response.status, elapsed_ms:elapsed()};
    }
    let payload = null;
    try { payload = await response.json(); }
    catch (_) {
      return {kind:'protocol_error', status:response.status,
              elapsed_ms:elapsed()};
    }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      return {kind:'protocol_error', status:response.status,
              elapsed_ms:elapsed()};
    }
    const code = payload.code;
    if (code !== 0) {
      if (code === undefined || code === null) {
        return {kind:'protocol_error', status:response.status,
                elapsed_ms:elapsed()};
      }
      const result = {kind:'error', status:response.status, code:String(code),
                      elapsed_ms:elapsed()};
      if (String(code) === '100000' && systemError3) result.system_error_3 = 1;
      return result;
    }
    const profile = payload.creator_profile;
    if (!profile || typeof profile !== 'object' || Array.isArray(profile)
        || Object.keys(profile).length === 0) {
      return {kind:'empty', status:response.status, code:0,
              elapsed_ms:elapsed()};
    }
    return {kind:'ok', status:response.status, code:0, profile,
            field_keys:Object.keys(profile).sort(), elapsed_ms:elapsed()};
  } catch (_) {
    return {kind:'network_error', elapsed_ms:elapsed()};
  }};
  try {
    // 截止时间覆盖响应头和JSON正文；超时必须取消仍在途的fetch。
    return await Promise.race([run(), new Promise(resolve => {
      timer = setTimeout(() => {
        resolve({kind:'timeout', elapsed_ms:elapsed()});
        controller.abort();
      }, deadlineMs);
    })]);
  } finally { clearTimeout(timer); }
}"""


@dataclass(frozen=True, slots=True)
class BrowserProfileResult:
    profile: dict
    field_keys: tuple[str, ...]
    status: int
    elapsed_ms: float | None


def _safe_code(value) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    normalized = str(value)
    return normalized if _SAFE_CODE.fullmatch(normalized) else None


def _status(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if 100 <= normalized <= 599 else None


def _elapsed(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(normalized) or normalized < 0:
        return None
    return normalized


def _failure(
    kind: OutcomeKind,
    *,
    subject: str | None,
    retryable: bool,
    remote_code: str | None = None,
    detail: str,
):
    return Outcome.failure(
        kind,
        subject=subject,
        retryable=retryable,
        remote_code=remote_code,
        detail=detail,
    )


def classify_profile_row(row, *, subject: str | None = None):
    """将浏览器返回行 fail-closed 分类；任何 detail 都是本地固定短码。"""
    if not isinstance(row, dict):
        return _failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=subject,
            retryable=True,
            detail="browser_profile_invalid_row",
        )

    kind = row.get("kind")
    status = _status(row.get("status"))
    code = _safe_code(row.get("code"))
    if kind == "verification":
        return _failure(
            OutcomeKind.PROFILE_WALL,
            subject=subject,
            retryable=True,
            remote_code="10000",
            detail="browser_profile_verification_required",
        )
    if kind == "ok":
        profile = row.get("profile")
        if (
            status is None
            or not 200 <= status < 300
            or row.get("code") != 0
            or not isinstance(profile, dict)
            or not profile
            or not all(isinstance(key, str) and key for key in profile)
        ):
            return _failure(
                OutcomeKind.PROTOCOL_ERROR,
                subject=subject,
                retryable=True,
                detail="browser_profile_invalid_success",
            )
        return Outcome.success(
            BrowserProfileResult(
                profile=dict(profile),
                field_keys=tuple(sorted(profile)),
                status=status,
                elapsed_ms=_elapsed(row.get("elapsed_ms")),
            ),
            subject=subject,
        )

    if kind == "empty":
        return _failure(
            OutcomeKind.AMBIGUOUS_EMPTY,
            subject=subject,
            retryable=True,
            detail="browser_profile_empty",
        )
    if kind == "timeout":
        return _failure(
            OutcomeKind.NETWORK_ERROR,
            subject=subject,
            retryable=True,
            detail="browser_profile_request_timeout",
        )
    if kind == "network_error":
        return _failure(
            OutcomeKind.NETWORK_ERROR,
            subject=subject,
            retryable=True,
            detail="browser_profile_network_error",
        )
    if kind == "error":
        status_code = str(status) if status is not None else None
        remote_code = code or status_code
        if status in {401, 403} or code in _AUTH_CODES:
            return _failure(
                OutcomeKind.AUTH_EXPIRED,
                subject=subject,
                retryable=False,
                remote_code=remote_code,
                detail="browser_profile_auth_expired",
            )
        if code == "10000":
            return _failure(
                OutcomeKind.PROFILE_WALL,
                subject=subject,
                retryable=True,
                remote_code=code,
                detail="browser_profile_wall",
            )
        if status == 429 or code == "16901008":
            return _failure(
                OutcomeKind.THROTTLED,
                subject=subject,
                retryable=True,
                remote_code=remote_code,
                detail="browser_profile_throttled",
            )
        if code == "100000" and row.get("system_error_3") in (1, True):
            return _failure(
                OutcomeKind.PROTOCOL_ERROR,
                subject=subject,
                retryable=True,
                remote_code=code,
                detail="browser_profile_transient_system_error_3",
            )
        return _failure(
            OutcomeKind.PROTOCOL_ERROR,
            subject=subject,
            retryable=True,
            remote_code=remote_code,
            detail="browser_profile_protocol_error",
        )

    return _failure(
        OutcomeKind.PROTOCOL_ERROR,
        subject=subject,
        retryable=True,
        detail="browser_profile_protocol_error",
    )


class BrowserProfileTransport:
    def __init__(self, page, profile_url: str, *, before_request=None):
        if not str(profile_url or "").strip():
            raise ValueError("profile_url 不能为空")
        self.page = page
        self.profile_url = str(profile_url)
        self.before_request = before_request

    def fetch(self, oec_id: str, profile_types: list[int]):
        if (
            not isinstance(oec_id, str)
            or not oec_id
            or not oec_id.isdigit()
        ):
            raise ValueError("oec_id 必须是数字字符串")
        if (
            not isinstance(profile_types, list)
            or not profile_types
            or any(type(value) is not int or value <= 0 for value in profile_types)
            or len(set(profile_types)) != len(profile_types)
        ):
            raise ValueError("profile_types 必须是非空整数列表")
        try:
            if self.before_request is not None:
                self.before_request()
            row = self.page.evaluate(
                PROFILE_FETCH_JS,
                {
                    "url": self.profile_url,
                    "oec": oec_id,
                    "types": list(profile_types),
                },
            )
        except Exception:
            return _failure(
                OutcomeKind.NETWORK_ERROR,
                subject=oec_id,
                retryable=True,
                detail="browser_profile_evaluate_error",
            )
        return classify_profile_row(row, subject=oec_id)


__all__ = [
    "PROFILE_FETCH_JS",
    "BrowserProfileResult",
    "BrowserProfileTransport",
    "classify_profile_row",
    "profile_url",
]
