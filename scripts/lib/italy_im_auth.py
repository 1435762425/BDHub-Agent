"""Italy IM authentication reads only; no conversation/create/send transport.

The only network destinations are three fixed Partner-EU GET endpoints. A token
response's IM URL is observed as metadata, never requested or automatically added
to an execution allowlist. Credentials and native context are returned in memory.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from urllib.parse import urlsplit

PARTNER_HOST = "https://partner.eu.tiktokshop.com"
IM_PAGE = PARTNER_HOST + "/partner/im?market=8"
PREFIX = "/api/v1/affiliate/partner/"
ENDPOINTS = {name: PARTNER_HOST + PREFIX + path for name, path in (
    ("info", "info"), ("im_id", "im/id/get"), ("token", "im/token/get"))}
SAFE_CODES = frozenset({"account_invalid", "account_disabled", "account_not_in_send_pool", "identity_invalid", "identity_not_own",
    "maintenance_due", "stopped", "transport_error", "http_rejected", "redirect_rejected", "response_invalid", "business_rejected",
    "verification_required", "market_identity_mismatch", "market_id_missing", "im_id_missing", "token_missing", "token_identity_mismatch",
    "im_endpoint_invalid", "guard_missing", "guard_busy", "guard_invalid", "identity_file_changed", "wall_timeout",
    "runtime_unavailable", "report_write_failed", "child_no_report", "probe_failed"})


class ItalyImAuthError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in SAFE_CODES else "probe_failed"
        super().__init__(self.code)


class ImProbeDeadline(BaseException):
    """A supervisor alarm must escape generic HTTP exception handlers."""


@dataclass(frozen=True, repr=False)
class ItalyImAuthContext:
    account_name: str
    im_id: str
    token: dict
    native_context: dict
    im_headers: dict
    next_request_at: float = 0.0


def _numeric(value, code):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,64}", value) or int(value) <= 0:
        raise ItalyImAuthError(code)
    return value


def _headers(source):
    if not isinstance(source, dict):
        raise ItalyImAuthError("identity_invalid")
    result, seen = {}, set()
    for key, value in source.items():
        if not isinstance(key, str) or not isinstance(value, str) or any(c in key + value for c in "\r\n"):
            raise ItalyImAuthError("identity_invalid")
        lower = key.lower()
        if lower in seen:
            raise ItalyImAuthError("identity_invalid")
        seen.add(lower)
        if not key.startswith(":") and lower not in {"host", "content-length", "connection", "origin", "referer"}:
            result[key] = value
    if not any(key.lower() == "cookie" and value.strip() for key, value in result.items()):
        raise ItalyImAuthError("identity_invalid")
    result.update(Origin=PARTNER_HOST, Referer=IM_PAGE)
    return result


def _code(value):
    return value if type(value) is int and -1_000_000_000 <= value <= 1_000_000_000 else None


def authenticate_it(account, identity, source_headers, report, *, http=None, maintenance_due=lambda: False,
                    stopped=lambda: False, monotonic=time.monotonic, sleep=time.sleep, on_update=lambda: None,
                    use_environment_proxy=True):
    """Read info → im/id/get → im/token/get exactly once under caller's guard.

    Caller must supervise wall time. The probe CLI uses a 60-second child alarm
    plus an independent 65-second parent deadline. Tests inject a fake HTTP
    object, monotonic clock and sleeper; this function never changes identity.
    """
    if getattr(account, "name", None) != "acc6":
        raise ItalyImAuthError("account_invalid")
    if getattr(account, "enabled", True) is False:
        raise ItalyImAuthError("account_disabled")
    if (getattr(identity, "market", None) != "it" or getattr(identity, "host", None) != PARTNER_HOST
            or str(getattr(identity, "aid", "")) != "360019" or str(getattr(identity, "im_market", "")) != "8"):
        raise ItalyImAuthError("identity_invalid")
    if getattr(identity, "partner_id_is_own", False) is not True:
        raise ItalyImAuthError("identity_not_own")
    partner_id = _numeric(getattr(identity, "partner_id", None), "identity_invalid")
    headers = _headers(source_headers)
    report.update(market="it", account="acc6", authTransport="pure_http", partnerApiHost="partner.eu.tiktokshop.com",
                  marketCode=8, aid="360019", environmentProxy=bool(use_environment_proxy), authReads=[],
                  browserInitializations=0, conversationCreateRequests=0, sendRequests=0, legacyDatabaseWrites=0)
    session, owned = http, False
    next_request_at = 0.0
    try:
        if session is None:
            try:
                import requests
                session = requests.Session()
                owned = True
                session.trust_env = bool(use_environment_proxy)
                session.auth = lambda request: request
                session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
            except Exception:
                raise ItalyImAuthError("runtime_unavailable") from None

        def read(stage, extra):
            nonlocal next_request_at
            if stopped():
                raise ItalyImAuthError("stopped")
            if maintenance_due():
                raise ItalyImAuthError("maintenance_due")
            sleep(max(0, next_request_at - monotonic()))
            if stopped():
                raise ItalyImAuthError("stopped")
            if maintenance_due():
                raise ItalyImAuthError("maintenance_due")
            start = monotonic()
            next_request_at = start + 1.0
            entry = {"stage": stage, "endpointPath": urlsplit(ENDPOINTS[stage]).path, "status": "inflight",
                     "httpStatus": None, "code": None, "latencyMs": None, "verificationRequired": False}
            report["authReads"].append(entry)
            on_update()
            try:
                response = session.get(ENDPOINTS[stage], headers=headers,
                                       params={"aid": "360019", "partner_id": partner_id, **extra},
                                       timeout=(5, 15), allow_redirects=False)
                status = getattr(response, "status_code", None)
                entry.update(httpStatus=status if type(status) is int and 100 <= status <= 599 else None, status="returned")
                response_headers = getattr(response, "headers", {})
                entry["verificationRequired"] = bool(response_headers.get("bdturing-verify"))
                try:
                    payload = response.json()
                except Exception:
                    payload = None
                entry["code"] = _code(payload.get("code")) if isinstance(payload, dict) else None
                if entry["verificationRequired"]:
                    raise ItalyImAuthError("verification_required")
                if type(status) is not int or status != 200:
                    raise ItalyImAuthError("redirect_rejected" if type(status) is int and 300 <= status < 400 else "http_rejected")
                if not isinstance(payload, dict):
                    raise ItalyImAuthError("response_invalid")
                if type(payload.get("code")) is not int or payload["code"] != 0:
                    raise ItalyImAuthError("verification_required" if payload.get("code") in (10000, "10000") else "business_rejected")
                data = payload.get("data", payload)
                if not isinstance(data, dict):
                    raise ItalyImAuthError("response_invalid")
                return data
            except ImProbeDeadline:
                entry.update(status="error", errorCode="wall_timeout")
                raise
            except ItalyImAuthError as error:
                entry["errorCode"] = error.code
                raise
            except Exception:
                entry.update(status="error", errorCode="transport_error")
                raise ItalyImAuthError("transport_error") from None
            finally:
                entry["latencyMs"] = round(max(0, monotonic() - start) * 1000, 1)
                on_update()

        partner = read("info", {"partner_type": 1})
        business = partner.get("partner_biz_role_info")
        markets = business.get("market_list") if isinstance(business, dict) else None
        if not isinstance(markets, list):
            raise ItalyImAuthError("market_identity_mismatch")
        matches = [row for row in markets if isinstance(row, dict) and str(row.get("market_region")) == "8"
                   and isinstance(row.get("type_list"), list)
                   and any(isinstance(role, dict) and str(role.get("partner_id")) == partner_id for role in row["type_list"])]
        if len(matches) != 1:
            raise ItalyImAuthError("market_identity_mismatch")
        market_row = matches[0]
        market_id = _numeric(market_row.get("market_id"), "market_id_missing")
        report.update(ownPartnerVerified=True, marketIdentityVerified=True)
        on_update()
        im = read("im_id", {"user_id": market_id, "type": 0})
        im_id = _numeric(im.get("im_id"), "im_id_missing")
        report["imIdentityVerified"] = True
        on_update()
        token = read("token", {"im_id": im_id})
        token_value = token.get("token")
        if not isinstance(token_value, str) or not token_value or len(token_value) > 65536 or "\r" in token_value or "\n" in token_value:
            raise ItalyImAuthError("token_missing")
        if token.get("im_id") is not None and _numeric(token["im_id"], "token_identity_mismatch") != im_id:
            raise ItalyImAuthError("token_identity_mismatch")
        try:
            endpoint = urlsplit(token.get("api_url") if isinstance(token.get("api_url"), str) else "")
            if (endpoint.scheme != "https" or not endpoint.hostname or not re.fullmatch(r"[a-z0-9.-]{1,253}", endpoint.hostname)
                    or endpoint.port is not None or endpoint.username or endpoint.password or endpoint.path not in ("", "/")
                    or endpoint.query or endpoint.fragment):
                raise ValueError()
        except Exception:
            raise ItalyImAuthError("im_endpoint_invalid") from None
        report.update(tokenPresent=True, tokenFields={key: type(token[key]).__name__ for key in ("token", "api_url", "app_id", "expire_time", "expires_in") if key in token},
                      apiHost=endpoint.hostname, imHostRequested=False, imHttpConversationVerified=False,
                      appIdPresent=token.get("app_id") is not None)
        on_update()
        native = {"partner": partner, "market_row": market_row, "market_id": market_id, "partner_id": partner_id, "market_region": "8"}
        im_headers = {key: value for key, value in headers.items() if key.lower() in {"user-agent", "accept-language"}}
        im_headers.update(Origin=PARTNER_HOST, Referer=PARTNER_HOST + "/")
        return ItalyImAuthContext("acc6", im_id, token, native, im_headers, next_request_at)
    finally:
        if owned and session is not None:
            session.close()


__all__ = ["ItalyImAuthError", "ImProbeDeadline", "ItalyImAuthContext", "authenticate_it"]
