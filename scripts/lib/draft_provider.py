"""Bounded DeepSeek Flash adapter; configuration reads and HTTP calls stay separate.

This module never enables a worker, retries a model request, or sends a business
message. The worker must establish authorization and reserve its budget before
calling ``call_model``. Errors retain only safe receipt metadata, including any
usage received before content validation failed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time as daytime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import signal
import threading
from urllib.parse import urlsplit

MODEL = "deepseek-flash"
ENDPOINT = "https://api.deepseek.com/chat/completions"
LEGACY_CONFIG = Path("/Users/bjn00003/BDHub/01-BDSystem-V2/config.yaml")
MAX_INPUT_BYTES = 24_000
MAX_OUTPUT_TOKENS = 1_200
MAX_RESPONSE_BYTES = 256 * 1024
SHANGHAI = timezone(timedelta(hours=8))
JSON_INSTRUCTION = "只返回一个有效 JSON 对象（{}），不要 Markdown 围栏或额外说明。"
PRICING = {
    "version": "deepseek-flash-cny-2026-09-12", "checkedOn": "2026-09-12", "currency": "CNY",
    "source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
    "offPeak": {"inputMiss": "1", "inputHit": "0.02", "output": "4"},
    "peak": {"inputMiss": "2", "inputHit": "0.04", "output": "8"},
}
SAFE_CODES = frozenset({
    "provider_not_configured", "provider_config_invalid", "provider_config_unavailable",
    "provider_input_invalid", "provider_input_too_large", "provider_limit_invalid", "provider_clock_invalid",
    "provider_runtime_unavailable", "provider_deadline_unavailable", "provider_http_error", "provider_redirect_rejected",
    "provider_timeout", "provider_network_error", "provider_response_invalid", "provider_response_too_large",
    "provider_model_mismatch", "provider_usage_invalid", "provider_output_invalid", "provider_output_truncated",
})
USAGE_KEYS = ("promptTokens", "cacheHitTokens", "cacheMissTokens", "completionTokens", "reasoningTokens", "totalTokens")


class DraftProviderError(RuntimeError):
    """Safe failure. The receipt never contains headers, credentials or raw errors."""

    def __init__(self, code, *, outcome="request_not_sent", receipt=None):
        self.code = code if code in SAFE_CODES else "provider_response_invalid"
        self.outcome = outcome if outcome in {"request_not_sent", "outcome_unknown"} else "outcome_unknown"
        self.receipt = deepcopy(receipt) if receipt is not None else None
        super().__init__(self.code)


class _ProviderDeadline(BaseException):
    """Must escape generic Exception handlers in HTTP/JSON libraries."""


class _DeadlineGuard:
    """Own one wall-clock alarm, without stealing another component's timer."""

    def __init__(self, timeout):
        self.timeout = timeout
        self.saved = None
        self.installed = False

    @staticmethod
    def expired(_signum, _frame):
        raise _ProviderDeadline()

    def start(self):
        if threading.current_thread() is not threading.main_thread():
            raise DraftProviderError("provider_deadline_unavailable")
        try:
            handler = signal.getsignal(signal.SIGALRM)
            timer = signal.getitimer(signal.ITIMER_REAL)
            # An existing active alarm belongs to its caller. Fail before send
            # rather than delaying, discarding or consuming that deadline.
            if timer[0] > 0:
                raise DraftProviderError("provider_deadline_unavailable")
            self.saved = (handler, timer)
            signal.signal(signal.SIGALRM, self.expired)
            self.installed = True
            signal.setitimer(signal.ITIMER_REAL, self.timeout)
        except DraftProviderError:
            raise
        except Exception:
            self.stop()
            raise DraftProviderError("provider_deadline_unavailable") from None

    def stop(self):
        if not self.installed:
            return
        self.installed = False
        handler, timer = self.saved
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
        finally:
            signal.signal(signal.SIGALRM, handler)
            signal.setitimer(signal.ITIMER_REAL, *timer)


def _key(value):
    if not isinstance(value, str) or not value.strip():
        raise DraftProviderError("provider_not_configured")
    value = value.strip()
    if not re.fullmatch(r"[^\x00-\x20\x7f]{1,512}", value):
        raise DraftProviderError("provider_config_invalid")
    return value


def _load_legacy_config():
    try:
        import yaml
        return yaml.safe_load(LEGACY_CONFIG.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise DraftProviderError("provider_not_configured") from None
    except Exception:
        raise DraftProviderError("provider_config_unavailable") from None


def resolve_credential(*, environ=None, config_loader=None):
    """Return only an in-memory key; caller cannot configure the endpoint/model."""
    environment = os.environ if environ is None else environ
    if environment.get("DEEPSEEK_API_KEY"):
        return _key(environment["DEEPSEEK_API_KEY"])
    try:
        document = (config_loader or _load_legacy_config)()
        reply = document.get("reply") if isinstance(document, dict) else None
        if not isinstance(reply, dict):
            raise DraftProviderError("provider_not_configured")
        raw_url = reply.get("base_url", "https://api.deepseek.com")
        if not isinstance(raw_url, str):
            raise DraftProviderError("provider_config_invalid")
        url = urlsplit(raw_url)
        if (url.scheme != "https" or url.hostname != "api.deepseek.com" or url.port not in (None, 443)
                or url.path not in ("", "/", "/v1", "/v1/") or url.username or url.password or url.query or url.fragment):
            raise DraftProviderError("provider_config_invalid")
        return _key(reply.get("api_key"))
    except DraftProviderError:
        raise
    except Exception:
        raise DraftProviderError("provider_config_invalid") from None


def provider_status(*, key_resolver=None):
    """Local configuration readiness only; never authenticates against the API."""
    error = None
    try:
        _key((key_resolver or resolve_credential)())
        ready = True
    except DraftProviderError as failure:
        ready, error = False, failure.code
    except Exception:
        ready, error = False, "provider_config_unavailable"
    return {"provider": "deepseek", "model": MODEL, "credentialReady": ready, "ready": ready,
            "endpointHost": "api.deepseek.com", "availabilityVerified": False, "errorCode": error,
            "pricing": deepcopy(PRICING), "inputByteLimit": MAX_INPUT_BYTES, "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "thinking": "disabled", "automaticRetries": 0, "wallDeadlineSeconds": 60, "requiresMainThread": True}


def _utc_now(clock):
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DraftProviderError("provider_clock_invalid")
    return value.astimezone(timezone.utc)


def _iso(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z") if value else None


def _tier(value):
    local = value.astimezone(SHANGHAI)
    hour = local.hour + local.minute / 60 + local.second / 3600
    return "peak" if local.weekday() < 5 and (9 <= hour < 12 or 14 <= hour < 18) else "off_peak"


def pricing_window(start, end):
    if start is None or end is None or end < start:
        return "unknown"
    if end - start > timedelta(days=7):
        return "cross_window"
    tiers = {_tier(start), _tier(end)}
    day = start.astimezone(SHANGHAI).date()
    last = end.astimezone(SHANGHAI).date()
    while day <= last:
        for hour in (9, 12, 14, 18):
            boundary = datetime.combine(day, daytime(hour), SHANGHAI)
            if start <= boundary <= end:
                tiers.add(_tier(boundary))
                before = boundary - timedelta(microseconds=1)
                if before >= start:
                    tiers.add(_tier(before))
        day += timedelta(days=1)
    return next(iter(tiers)) if len(tiers) == 1 else "cross_window"


def normalize_usage(raw):
    """Preserve unavailable counts as None and validate all supplied identities.

    ``cached_tokens`` is an officially documented synonym, not an assumed hit.
    No other missing quantity is inferred or silently zero-filled.
    """
    usage = dict.fromkeys(USAGE_KEYS)
    if not isinstance(raw, dict):
        return usage, raw is None
    valid = True

    def count(value):
        nonlocal valid
        if value is None:
            return None
        if type(value) is not int or not 0 <= value <= 9_007_199_254_740_991:
            valid = False
            return None
        return value

    for source, target in (("prompt_tokens", "promptTokens"), ("prompt_cache_hit_tokens", "cacheHitTokens"),
                           ("prompt_cache_miss_tokens", "cacheMissTokens"), ("completion_tokens", "completionTokens"),
                           ("total_tokens", "totalTokens")):
        usage[target] = count(raw.get(source))
    detail = raw.get("prompt_tokens_details")
    if detail is not None and not isinstance(detail, dict):
        valid = False
    cached = count(detail.get("cached_tokens")) if isinstance(detail, dict) else None
    if usage["cacheHitTokens"] is None and "prompt_cache_hit_tokens" not in raw:
        usage["cacheHitTokens"] = cached
    elif cached is not None and usage["cacheHitTokens"] != cached:
        valid = False
    output_detail = raw.get("completion_tokens_details")
    if output_detail is not None and not isinstance(output_detail, dict):
        valid = False
    usage["reasoningTokens"] = count(output_detail.get("reasoning_tokens")) if isinstance(output_detail, dict) else None
    prompt, hit, miss = (usage[key] for key in ("promptTokens", "cacheHitTokens", "cacheMissTokens"))
    completion, reasoning, total = (usage[key] for key in ("completionTokens", "reasoningTokens", "totalTokens"))
    if prompt is not None and hit is not None and miss is not None and hit + miss != prompt:
        valid = False
    if prompt is not None and any(value is not None and value > prompt for value in (hit, miss)):
        valid = False
    if total is not None and prompt is not None and completion is not None and total != prompt + completion:
        valid = False
    if reasoning is not None and completion is not None and reasoning > completion:
        valid = False
    return usage, valid


def estimate_cost(usage, start, end, *, usage_valid=True, request_sent=True):
    window = pricing_window(start, end)
    result = {"estimatedCny": None, "upperBoundCny": None, "complete": False,
              "pricingVersion": PRICING["version"], "window": window}
    if not request_sent:
        return {**result, "estimatedCny": "0", "upperBoundCny": "0", "complete": True}
    if not usage_valid or any(usage.get(key) is None for key in ("cacheHitTokens", "cacheMissTokens", "completionTokens")):
        return result

    def price(rates):
        # reasoningTokens is already part of completionTokens: never add it twice.
        amount = (Decimal(usage["cacheHitTokens"]) * Decimal(rates["inputHit"])
                  + Decimal(usage["cacheMissTokens"]) * Decimal(rates["inputMiss"])
                  + Decimal(usage["completionTokens"]) * Decimal(rates["output"])) / Decimal(1_000_000)
        return format(amount, "f")

    result["upperBoundCny"] = price(PRICING["peak"])
    if window in {"peak", "off_peak"}:
        result.update(estimatedCny=price(PRICING["peak" if window == "peak" else "offPeak"]), complete=True)
    return result


def _messages(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise DraftProviderError("provider_input_invalid")
    result = []
    for item in value:
        if (not isinstance(item, dict) or set(item) != {"role", "content"}
                or not isinstance(item["role"], str)
                or item["role"] not in {"system", "user", "assistant"}
                or not isinstance(item["content"], str) or not item["content"].strip()):
            raise DraftProviderError("provider_input_invalid")
        result.append(dict(item))
    if not any(item["role"] == "user" for item in result):
        raise DraftProviderError("provider_input_invalid")
    for item in result:
        if item["role"] == "system":
            item["content"] += "\n\n" + JSON_INSTRUCTION
            break
    else:
        result.insert(0, {"role": "system", "content": JSON_INSTRUCTION})
    try:
        size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (ValueError, UnicodeError):
        raise DraftProviderError("provider_input_invalid") from None
    if size > MAX_INPUT_BYTES:
        raise DraftProviderError("provider_input_too_large")
    return result


def _response_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value) else None


def _json_object(content):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError()

    parsed = json.loads(content, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(parsed, dict):
        raise ValueError()


def call_model(messages, *, max_output_tokens=1200, timeout=60, http=None, clock=None, key_resolver=None):
    """One fixed official request, returning a safe successful receipt or error.

    Injected ``http`` exposes requests-compatible ``post`` and remains caller
    owned. ``clock`` returns timezone-aware datetimes. ``key_resolver`` returns a
    key string. These hooks exist for offline tests; they are not user options.
    """
    clock = clock or (lambda: datetime.now(timezone.utc))
    start = end = None
    sent = False
    session = http
    owned = False
    usage = dict.fromkeys(USAGE_KEYS)
    usage_valid = True
    response_id = None
    deadline = None

    def receipt():
        return {"model": MODEL, "responseId": response_id, "usage": dict(usage),
                "cost": estimate_cost(usage, start, end, usage_valid=usage_valid, request_sent=sent),
                "startedAt": _iso(start), "finishedAt": _iso(end)}

    try:
        start = _utc_now(clock)
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS or isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise DraftProviderError("provider_limit_invalid")
        prepared = _messages(messages)
        key = _key((key_resolver or resolve_credential)())
        if session is None:
            try:
                import requests
                session = requests.Session()
                owned = True
                # Keep only the selected Bearer key; do not silently load netrc auth.
                session.auth = lambda request: request
                # requests defaults to zero transport retries; explicitly mount it.
                session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
            except Exception:
                raise DraftProviderError("provider_runtime_unavailable") from None
        deadline = _DeadlineGuard(timeout)
        deadline.start()
        sent = True
        try:
            response = session.post(ENDPOINT, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                                    json={"model": MODEL, "messages": prepared, "max_tokens": max_output_tokens,
                                          "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}, "stream": False},
                                    timeout=timeout, allow_redirects=False)
        except Exception as error:
            code = "provider_timeout" if isinstance(error, TimeoutError) or type(error).__name__ in {"Timeout", "ReadTimeout", "ConnectTimeout"} else "provider_network_error"
            raise DraftProviderError(code, outcome="outcome_unknown") from None
        raw = getattr(response, "content", None)
        if isinstance(raw, (bytes, bytearray)) and len(raw) > MAX_RESPONSE_BYTES:
            raise DraftProviderError("provider_response_too_large", outcome="outcome_unknown")
        status = getattr(response, "status_code", None)
        try:
            payload = response.json()
        except Exception:
            code = "provider_redirect_rejected" if type(status) is int and 300 <= status < 400 else "provider_http_error" if type(status) is int and status != 200 else "provider_response_invalid"
            raise DraftProviderError(code, outcome="outcome_unknown") from None
        if not isinstance(payload, dict):
            raise DraftProviderError("provider_response_invalid", outcome="outcome_unknown")
        usage, usage_valid = normalize_usage(payload.get("usage"))
        response_id = _response_id(payload.get("id"))
        if type(status) is not int or status != 200:
            raise DraftProviderError("provider_redirect_rejected" if type(status) is int and 300 <= status < 400 else "provider_http_error", outcome="outcome_unknown")
        if payload.get("model") != MODEL:
            usage_valid = False  # Model-specific pricing is not proven for an unexpected response model.
            raise DraftProviderError("provider_model_mismatch", outcome="outcome_unknown")
        if not usage_valid:
            raise DraftProviderError("provider_usage_invalid", outcome="outcome_unknown")
        try:
            choices = payload["choices"]
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ValueError()
            choice = choices[0]
            if choice.get("finish_reason") == "length":
                raise DraftProviderError("provider_output_truncated", outcome="outcome_unknown")
            if choice.get("finish_reason") != "stop" or not isinstance(choice.get("message"), dict):
                raise ValueError()
            content = choice["message"].get("content")
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_RESPONSE_BYTES:
                raise ValueError()
            _json_object(content)
        except DraftProviderError:
            raise
        except Exception:
            raise DraftProviderError("provider_output_invalid", outcome="outcome_unknown") from None
        end = _utc_now(clock)
        return {"content": content, **receipt()}
    except _ProviderDeadline:
        if deadline is not None:
            deadline.stop()
        try:
            end = _utc_now(clock)
        except Exception:
            end = None
        raise DraftProviderError("provider_timeout", outcome="outcome_unknown" if sent else "request_not_sent", receipt=receipt()) from None
    except DraftProviderError as error:
        if deadline is not None:
            deadline.stop()
        try:
            end = _utc_now(clock)
        except Exception:
            end = None
        raise DraftProviderError(error.code, outcome="outcome_unknown" if sent else "request_not_sent", receipt=receipt()) from None
    except Exception:
        if deadline is not None:
            deadline.stop()
        try:
            end = _utc_now(clock)
        except Exception:
            end = None
        raise DraftProviderError("provider_response_invalid" if sent else "provider_config_unavailable",
                                 outcome="outcome_unknown" if sent else "request_not_sent", receipt=receipt()) from None
    finally:
        if deadline is not None:
            deadline.stop()
        if owned and session is not None:
            try:
                session.close()
            except Exception:
                pass


__all__ = ["DraftProviderError", "provider_status", "call_model", "normalize_usage", "estimate_cost", "resolve_credential"]
