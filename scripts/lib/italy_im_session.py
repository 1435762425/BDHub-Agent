"""IT HTTP IM reads on the host observed by the 2026-09-12 EU auth probe.

Only cmd203, cmd608 and cmd301 can leave this adapter. No SendPacket, send
validator monkeypatch, get-or-create, receipt mutation, or message-body export.
The Pigeon schema comes from the retained 12c8c68... SDK bundle: InitV2 field2
contains ConversationInfoV2; its field50 is core info, whose field11 is ext.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import hashlib
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import urlsplit

from lib.italy_im_auth import ImProbeDeadline, PARTNER_HOST

IM_HOST = "oec-im-tt-i18n.tiktokglobalshopv.com"
PATHS = {203: "/v2/message/get_by_user_init", 608: "/v2/conversation/get_info", 301: "/v1/message/get_by_conversation"}
SAFE_CODES = frozenset({"im_auth_context_invalid", "im_endpoint_unapproved", "im_cookie_forbidden", "im_command_forbidden",
    "im_input_invalid", "im_runtime_unavailable", "im_auth_expired", "maintenance_due", "stopped", "im_transport_error",
    "im_http_rejected", "im_redirect_rejected", "im_verification_required", "im_response_invalid", "im_correlation_mismatch",
    "im_remote_rejected", "im_conversation_identity_mismatch", "im_conversation_market_mismatch", "im_duplicate_identity",
    "im_conversation_unverified", "im_history_identity_mismatch", "wall_timeout"})


class ItalyImReadError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in SAFE_CODES else "im_response_invalid"
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class VerifiedConversation:
    conversation_id: str
    full_cid: bytes
    conversation_type: int
    ticket: bytes
    oec_id: str
    evidence_ref: str = ""


def _proto():
    sys.dont_write_bytecode = True
    legacy = "/Users/bjn00003/BDHub/01-BDSystem-V2"
    if legacy not in sys.path:
        sys.path.insert(0, legacy)
    from bdhub.send import http_protocol
    return http_protocol


def _id(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 19 or not 0 < int(value) <= (1 << 63) - 1:
        raise ItalyImReadError("im_input_invalid")
    return value


def _extension(wire, raw):
    core = wire.wire_fields(wire.one(raw, 50, b""))
    result = {}
    for value in core.get(11, []):
        pair = wire.wire_fields(value)
        key, item = wire.one(pair, 1), wire.one(pair, 2)
        if not isinstance(key, bytes) or not isinstance(item, bytes) or key in result:
            raise ItalyImReadError("im_response_invalid")
        result[key] = item
    return result


def _market_matches(ext):
    if ext.get(b"market_region") not in (None, b"8") or ext.get(b"shop_region") not in (None, b"IT", b"it"):
        raise ItalyImReadError("im_conversation_market_mismatch")


def decode_initial_conversations(body, wire=None):
    """Return CID/OEC metadata only, deliberately ignoring MessageBody field1."""
    wire = wire or _proto()
    try:
        parsed = wire.wire_fields(body)
        result, seen, invalid, other_market = [], {}, 0, 0
        for raw in parsed.get(2, []):
            try:
                info = wire.wire_fields(raw)
                ext = _extension(wire, info)
                _market_matches(ext)
                cid = _id(wire.one(info, 2))
                raw_oec = ext.get(b"creator_oec_id")
                oec = _id(raw_oec.decode("ascii") if isinstance(raw_oec, bytes) else None)
                kind = wire.one(info, 3)
                if type(kind) is not int or not 0 <= kind <= 10:
                    raise ItalyImReadError("im_response_invalid")
                item = {"conversationId": cid, "oecId": oec, "conversationType": kind,
                        "ticketPresent": bool(wire.one(info, 4)), "identitySource": "conversation_core.creator_oec_id"}
            except ItalyImReadError as error:
                if error.code == "im_conversation_market_mismatch":
                    other_market += 1
                else:
                    invalid += 1
                continue
            except Exception:
                invalid += 1
                continue
            if cid in seen:
                if seen[cid]["oecId"] != oec or seen[cid]["conversationType"] != kind:
                    raise ItalyImReadError("im_duplicate_identity")
                continue
            seen[cid] = item
            result.append(item)
        more = wire.one(parsed, 5, 0)
        cursor = wire.one(parsed, 4, 0)
        if type(more) is not int or more not in (0, 1) or type(cursor) is not int or not 0 <= cursor <= (1 << 63) - 1:
            raise ItalyImReadError("im_response_invalid")
        return {"conversations": result, "hasMore": bool(more), "nextCursor": str(cursor),
                "invalidConversations": invalid, "otherMarketConversations": other_market,
                "messageBodiesDiscarded": len(parsed.get(1, [])), "pageOnly": True, "completeInbox": False}
    except ItalyImReadError:
        raise
    except Exception:
        raise ItalyImReadError("im_response_invalid") from None


class ItalyImReadSession:
    def __init__(self, auth, report, *, http=None, monotonic=time.monotonic, sleep=time.sleep,
                 maintenance_due=lambda: False, stopped=lambda: False, on_update=lambda: None,
                 use_environment_proxy=True, sequence=None):
        self.wire = _proto()
        try:
            if auth.account_name != "acc6" or auth.native_context.get("market_region") != "8":
                raise ValueError()
            endpoint = urlsplit(auth.token.get("api_url", ""))
            if (endpoint.scheme != "https" or endpoint.netloc != IM_HOST or endpoint.path not in ("", "/")
                    or endpoint.query or endpoint.fragment):
                raise ItalyImReadError("im_endpoint_unapproved")
            self.im_id = _id(auth.im_id)
            self.token = auth.token["token"]
            if not isinstance(self.token, str) or not self.token or any(c in self.token for c in "\r\n"):
                raise ValueError()
            if any(key.lower() in {"cookie", "authorization", "proxy-authorization"} for key in auth.im_headers):
                raise ItalyImReadError("im_cookie_forbidden")
            self.headers = {key.lower(): value for key, value in auth.im_headers.items() if key.lower() in {"user-agent", "accept-language"}}
            self.headers.update({"content-type": "application/x-protobuf", "origin": PARTNER_HOST, "referer": PARTNER_HOST + "/"})
            self.next_request_at = float(getattr(auth, "next_request_at", 0))
            if not math.isfinite(self.next_request_at) or self.next_request_at < 0:
                raise ValueError()
        except ItalyImReadError:
            raise
        except Exception:
            raise ItalyImReadError("im_auth_context_invalid") from None
        self.report, self.monotonic, self.sleep = report, monotonic, sleep
        self.maintenance_due, self.stopped, self.on_update = maintenance_due, stopped, on_update
        self.sequence = sequence if sequence is not None else secrets.randbelow(1_000_000_000) + 1
        if type(self.sequence) is not int or self.sequence < 1:
            raise ItalyImReadError("im_input_invalid")
        self.expires_at = monotonic() + 15 * 60
        self.verified = {}
        self.session, self.owned = http, False
        if self.session is None:
            try:
                import requests
                self.session = requests.Session()
                self.owned = True
                self.session.trust_env = bool(use_environment_proxy)
                self.session.auth = lambda request: request
                self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
            except Exception:
                self.close()
                raise ItalyImReadError("im_runtime_unavailable") from None
        report.setdefault("imHostRequested", False)
        report.update(imReadHost=IM_HOST, imReads=[], sendRequests=0, conversationCreateRequests=0, messageBodiesStored=False)

    def close(self):
        if getattr(self, "owned", False) and self.session is not None:
            self.session.close()
            self.session = None

    def __enter__(self): return self
    def __exit__(self, *_): self.close()

    def _read(self, command, body):
        if type(command) is not int or command not in PATHS or not isinstance(body, bytes):
            raise ItalyImReadError("im_command_forbidden")
        if self.stopped(): raise ItalyImReadError("stopped")
        if self.maintenance_due(): raise ItalyImReadError("maintenance_due")
        if self.monotonic() >= self.expires_at: raise ItalyImReadError("im_auth_expired")
        self.sleep(max(0, self.next_request_at - self.monotonic()))
        if self.stopped(): raise ItalyImReadError("stopped")
        if self.maintenance_due(): raise ItalyImReadError("maintenance_due")
        if self.monotonic() >= self.expires_at: raise ItalyImReadError("im_auth_expired")
        self.sequence += 1
        sequence, wire = self.sequence, self.wire
        packet = (wire.vi(1, command) + wire.vi(2, sequence) + wire.vb(3, "1.2.2") + wire.vb(4, self.token)
                  + wire.vi(5, 3) + wire.vi(6, 0) + wire.vb(7, "5dd76f3:master") + wire.vb(8, wire.vb(command, body))
                  + wire.vb(9, self.im_id) + wire.vb(11, "web") + wire.vi(18, 2))
        # Build and verify only a single permitted read body. Never call the MX
        # send-packet validator, nor alter its READ_HOST/send capability gates.
        fields = wire.wire_fields(packet)
        if wire.one(fields, 1) != command or set(wire.wire_fields(wire.one(fields, 8))) != {command}:
            raise ItalyImReadError("im_command_forbidden")
        start = self.monotonic()
        self.next_request_at = start + 1
        entry = {"command": command, "endpointPath": PATHS[command], "status": "inflight", "httpStatus": None,
                 "sequenceMatches": None, "outerStatus": None, "responseBytes": None, "latencyMs": None}
        self.report["imReads"].append(entry)
        self.on_update()
        response_received = False
        try:
            self.report["imHostRequested"] = True
            self.on_update()
            response = self.session.post("https://" + IM_HOST + PATHS[command], headers=self.headers, data=packet,
                                         timeout=(5, 15), allow_redirects=False)
            response_received = True
            status = getattr(response, "status_code", None)
            entry.update(httpStatus=status if type(status) is int else None, status="returned")
            if getattr(response, "headers", {}).get("bdturing-verify"):
                raise ItalyImReadError("im_verification_required")
            if type(status) is not int or status != 200:
                raise ItalyImReadError("im_redirect_rejected" if type(status) is int and 300 <= status < 400 else "im_http_rejected")
            data = response.content
            if not isinstance(data, bytes) or len(data) > 8_000_000:
                raise ItalyImReadError("im_response_invalid")
            entry["responseBytes"] = len(data)
            outer = wire.wire_fields(data)
            entry["sequenceMatches"] = wire.one(outer, 2) == sequence
            outer_status = wire.one(outer, 3, 0)
            entry["outerStatus"] = outer_status if type(outer_status) is int else None
            if wire.one(outer, 1) != command or not entry["sequenceMatches"]:
                raise ItalyImReadError("im_correlation_mismatch")
            if outer_status != 0:
                raise ItalyImReadError("im_remote_rejected")
            decoded = wire.decode_envelope(data, cmd=command, sequence=sequence)
            if command not in decoded:
                raise ItalyImReadError("im_response_invalid")
            return wire.one(decoded, command), data, sequence
        except ImProbeDeadline:
            entry.update(status="error", errorCode="wall_timeout")
            raise
        except ItalyImReadError as error:
            entry["errorCode"] = error.code
            raise
        except Exception:
            code = "im_response_invalid" if response_received else "im_transport_error"
            entry.update(status="error", errorCode=code)
            raise ItalyImReadError(code) from None
        finally:
            entry["latencyMs"] = round(max(0, self.monotonic() - start) * 1000, 1)
            self.on_update()

    def initialize(self, cursor=0):
        if type(cursor) is not int or not 0 <= cursor <= (1 << 63) - 1:
            raise ItalyImReadError("im_input_invalid")
        body, _, _ = self._read(203, self.wire.vi(1, cursor))
        result = decode_initial_conversations(body, self.wire)
        self.report.update(imInitializationVerified=True, initialConversationCount=len(result["conversations"]),
                           initialHasMore=result["hasMore"], initialInvalidConversations=result["invalidConversations"],
                           initialOtherMarketConversations=result["otherMarketConversations"], messageBodiesDiscarded=result["messageBodiesDiscarded"])
        self.on_update()
        return result

    def conversation(self, cid, oec_id, *, conversation_type=2):
        cid, oec_id = _id(cid), _id(oec_id)
        if type(conversation_type) is not int or not 0 <= conversation_type <= 10:
            raise ItalyImReadError("im_input_invalid")
        wire = self.wire
        body, raw, sequence = self._read(608, wire.vb(1, cid) + wire.vi(2, int(cid)) + wire.vi(3, conversation_type))
        try:
            decoded = wire.decode_conversation(raw, sequence=sequence, cid=cid, oec=oec_id)
            info = wire.wire_fields(wire.one(wire.wire_fields(body), 1))
            _market_matches(_extension(wire, info))
            if decoded.conversation_type != conversation_type:
                raise ItalyImReadError("im_conversation_identity_mismatch")
            result = VerifiedConversation(cid, decoded.full_cid, decoded.conversation_type, decoded.ticket, oec_id,
                                          "it-im-conversation:" + hashlib.sha256(raw).hexdigest())
        except ItalyImReadError:
            raise
        except Exception:
            raise ItalyImReadError("im_conversation_identity_mismatch") from None
        self.verified[cid] = result
        self.report["verifiedConversationCount"] = len(self.verified)
        self.on_update()
        return result

    def history_summary(self, conversation):
        if not isinstance(conversation, VerifiedConversation) or self.verified.get(conversation.conversation_id) is not conversation:
            raise ItalyImReadError("im_conversation_unverified")
        wire = self.wire
        request = (wire.vb(1, conversation.full_cid) + wire.vi(2, conversation.conversation_type)
                   + wire.vi(3, int(conversation.conversation_id)) + wire.vi(4, 1) + wire.vi(5, 0) + wire.vi(6, 20))
        body, _, _ = self._read(301, request)
        try:
            decoded = wire.wire_fields(body)
            rows = decoded.get(1, [])
            for item in rows:
                message = wire.wire_fields(item)
                if wire.one(message, 1) != conversation.full_cid or wire.one(message, 5) != int(conversation.conversation_id):
                    raise ItalyImReadError("im_history_identity_mismatch")
            more = wire.one(decoded, 3, 0)
            if type(more) is not int or more not in (0, 1):
                raise ItalyImReadError("im_response_invalid")
            result = {"messageCount": len(rows), "hasMore": bool(more), "identityVerified": True, "messageBodiesStored": False}
        except ItalyImReadError:
            raise
        except Exception:
            raise ItalyImReadError("im_response_invalid") from None
        self.report["historyReadCount"] = self.report.get("historyReadCount", 0) + 1
        self.on_update()
        return result
