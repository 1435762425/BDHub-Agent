"""Bounded IT canary write primitives, separate from all legacy MX gates.

This adapter owns no authorization, persistent intent, shared account quota or
retry policy. Every write needs the runner's last-moment dispatch acknowledgement.
The existing read session supplies authentication, conversation proofs and pacing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
import time
from urllib.parse import urlsplit
from uuid import UUID

from lib.italy_im_auth import ItalyImAuthContext, ImProbeDeadline, PARTNER_HOST
from lib.italy_im_session import ItalyImReadSession, ItalyImReadError, VerifiedConversation, IM_HOST,SG_IM_HOST,EU_IM_REGIONS, _proto

CREATE_PATH = "/api/v1/im/conversation/create"
SEND_PATH = "/v1/message/send"
CARD_CONTENT = "[商品列表]"
CARD_TITLE_KEY = "ttspc_im_communication_list_preview_message_title3"
CARD_ORIGIN = PARTNER_HOST + "/api/v1/affiliate/partner/im/product_list/list"
CARD_SENDERS=frozenset({('it','acc6'),('br','acc1'),('my','acc8'),('uk','acc11')})
SAFE_CODES = frozenset({"it_delivery_auth_invalid", "it_delivery_session_mismatch", "it_delivery_input_invalid",
    "it_delivery_conversation_unverified", "it_delivery_dispatch_not_allowed", "it_delivery_duplicate_dispatch",
    "it_delivery_stopped", "it_delivery_maintenance_due", "it_delivery_auth_expired", "it_delivery_create_unknown",
    "it_delivery_send_unknown", "it_delivery_send_rejected", "it_delivery_receipt_mismatch", "it_delivery_history_mismatch",
    "it_delivery_history_unavailable", "it_delivery_history_not_found", "it_delivery_ambiguous", "it_delivery_wall_timeout",
    "it_delivery_card_binding_invalid"})


class ItalyImDeliveryError(RuntimeError):
    def __init__(self, code, *, outcome="not_submitted", native_status=None, check_code=None, check_message=None, response_ref=None):
        self.code = code if code in SAFE_CODES else "it_delivery_input_invalid"
        self.outcome = outcome if outcome in {"not_submitted", "result_unknown", "rejected"} else "result_unknown"
        self.native_status = native_status if type(native_status) is int else None
        self.check_code = check_code if type(check_code) is int else None
        self.check_message=check_message if isinstance(check_message,str) else None
        self.response_ref=response_ref
        super().__init__(self.code)


def _id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 19 or not value.isascii() or not value.isdigit() or not 0 < int(value) <= (1 << 63) - 1:
        raise ItalyImDeliveryError("it_delivery_input_invalid")
    return value


def _native_id(value):
    return _id(str(value) if type(value) is int else value)


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
            raise ValueError()
        return value
    except (ValueError, AttributeError, TypeError):
        raise ItalyImDeliveryError("it_delivery_input_invalid") from None


def _text(value):
    try:
        if not isinstance(value, str) or not value.strip() or len(value) > 900 or "\x00" in value:
            raise ValueError()
        encoded = value.encode("utf-8")
        if len(encoded) > 4096:
            raise ValueError()
        return encoded
    except (ValueError, UnicodeError):
        raise ItalyImDeliveryError("it_delivery_input_invalid") from None


@dataclass(frozen=True, repr=False)
class ItalyVerifiedProductCard:
    """Frozen result of the caller's current IT/ACC6 read-only card verification.

    This is a provenance contract, not a network verifier or a time-to-live policy.
    The runner must check that this proof is still current at dispatch time.
    """
    product_id: str
    list_id: str
    campaign_id: str
    campaign_name: str
    list_name: str
    market: str
    account_name: str
    verified_at: str
    origin: str
    evidence_sha256: str
    binding_sha256: str
    verified: bool
    title_key: str = CARD_TITLE_KEY

    def __post_init__(self):
        _verified_card(self)


def card_binding_sha256(card: ItalyVerifiedProductCard | dict) -> str:
    """Hash the exact card/market/account fields, before constructing a descriptor."""
    keys = ("product_id", "list_id", "campaign_id", "campaign_name", "list_name", "market", "account_name", "title_key")
    try:
        values = {key: (card.get(key, CARD_TITLE_KEY if key == "title_key" else None) if isinstance(card, dict) else getattr(card, key)) for key in keys}
        return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except Exception:
        raise ItalyImDeliveryError("it_delivery_card_binding_invalid") from None


def _verified_card(card):
    try:
        if not isinstance(card, ItalyVerifiedProductCard) or (card.market,card.account_name) not in CARD_SENDERS or card.verified is not True:
            raise ValueError()
        _id(card.product_id);_id(card.list_id)
        if card.campaign_id != "0":_id(card.campaign_id)
        origin=urlsplit(card.origin)
        if card.title_key != CARD_TITLE_KEY or origin.scheme!='https' or not origin.hostname or \
                not (origin.hostname=='tiktokshop.com' or origin.hostname.endswith('.tiktokshop.com')) or \
                origin.path!='/api/v1/affiliate/partner/im/product_list/list' or origin.query or origin.fragment:
            raise ValueError()
        for name in (card.campaign_name, card.list_name):
            if not isinstance(name, str) or len(name) > 1000 or "\x00" in name:
                raise ValueError()
            name.encode("utf-8")
        observed = datetime.fromisoformat(card.verified_at.replace("Z", "+00:00"))
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError()
        if any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in (card.evidence_sha256, card.binding_sha256)):
            raise ValueError()
        if card.binding_sha256 != card_binding_sha256(card):
            raise ValueError()
        return card
    except Exception:
        raise ItalyImDeliveryError("it_delivery_card_binding_invalid") from None


def _card_extensions(card):
    card = _verified_card(card)
    return {"type": "product_list", "product_id": card.product_id, "list_id": card.list_id,
            "campaign_id": card.campaign_id, "campaign_name": card.campaign_name,
            "list_name": card.list_name, "starling_content_key": card.title_key}


def _native(auth):
    try:
        if not isinstance(auth, ItalyImAuthContext):
            raise ValueError()
        context = auth.native_context
        market=str(context.get('market') or 'it')
        account_name=str(context.get('account') or auth.account_name)
        market_region=str(context.get('market_region') or '')
        if (market,account_name) not in CARD_SENDERS or account_name!=auth.account_name or not market_region.isdigit():
            raise ValueError()
        endpoint = urlsplit(auth.token.get("api_url", ""))
        expected_host=str(context.get('im_host') or (IM_HOST if market_region in EU_IM_REGIONS else SG_IM_HOST))
        if endpoint.scheme != "https" or endpoint.hostname != expected_host or endpoint.port is not None or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment:
            raise ValueError()
        token = auth.token.get("token")
        if not isinstance(token, str) or not token or any(char in token for char in "\r\n"):
            raise ValueError()
        im_id = _id(auth.im_id)
        if any(key.lower() in {"cookie", "authorization", "proxy-authorization"} for key in auth.im_headers):
            raise ValueError()
        partner, row = context["partner"], context["market_row"]
        market_id, partner_id = _native_id(context["market_id"]), _native_id(context["partner_id"])
        if str(row.get("market_region")) != market_region or _native_id(row.get("market_id")) != market_id:
            raise ValueError()
        roles = {}
        for kind in (1, 4):
            values = {_native_id(role["partner_id"]) for role in row.get("type_list", [])
                      if isinstance(role, dict) and type(role.get("type")) is int and role["type"] == kind and role.get("partner_id")}
            if len(values) > 1:
                raise ValueError()
            roles[kind] = next(iter(values), "")
        if partner_id not in (roles[1], roles[4]):
            raise ValueError()
        business = partner.get("partner_biz_role_info", {})
        own_rows = [item for item in business.get("market_list", []) if isinstance(item, dict)
                    and str(item.get("market_region")) == market_region and str(item.get("market_id")) == market_id]
        if len(own_rows) != 1 or own_rows[0] != row:
            raise ValueError()
        company = (partner.get("partner_info") or {}).get("company_name")
        region = business.get("company_region")
        if not isinstance(company, str) or not company.strip() or len(company) > 500 or type(region) not in (str, int) or not str(region).strip():
            raise ValueError()
        app_id = auth.token.get("app_id");_native_id(app_id)
        avatar = partner.get("avatar_url") or ""
        if not isinstance(avatar, str) or len(avatar) > 4000:
            raise ValueError()
        portal=urlsplit(str(context.get('partner_host') or PARTNER_HOST))
        if portal.scheme!='https' or not portal.hostname:raise ValueError()
        partner_host=f'{portal.scheme}://{portal.netloc}';shop_region='GB' if market=='uk' else market.upper()
        return {"im_id": im_id, "token": token, "market_id": market_id, "partner_id": partner_id, "tap_id": roles[4], "cap_id": roles[1],
                "company_region": str(region), "agency_company_name": company, "agency_avatar": avatar, "app_id": app_id,
                "api_url": "https://" + expected_host,'im_host':expected_host,'partner_host':partner_host,
                'market':market,'market_region':market_region,'shop_region':shop_region,'account':account_name}
    except (KeyError, TypeError, ValueError, AttributeError, ItalyImDeliveryError):
        raise ItalyImDeliveryError("it_delivery_auth_invalid") from None


def build_create_payload(auth: ItalyImAuthContext, oec_id: str, *, created_at_ms: int) -> dict:
    """Native Partner participants/biz_hook_ext, with IT identities from this auth only."""
    oec_id = _id(oec_id);native = _native(auth)
    if type(created_at_ms) is not int or not 0 <= created_at_ms <= 9_007_199_254_740_991:
        raise ItalyImDeliveryError("it_delivery_input_invalid")
    agency = native["market_id"]
    return {"app_id": native["app_id"], "participants": [
        {"role": 0, "uid": oec_id, "extra": {"sender_role": "1", "creator_oec_id": oec_id}},
        {"role": 1, "uid": agency, "extra": {"sender_role": "4", "agency_market_id": agency, "tap_id": native["tap_id"], "cap_id": native["cap_id"]}}],
        "options": {"api_url": native["api_url"]}, "biz_hook_ext": {"1": oec_id, "4": agency,
            "createConversationTime": str(created_at_ms), "agency_market_id": agency, "company_region": native["company_region"],
            "agency_company_name": native["agency_company_name"], "market_region": native['market_region'], "agency_avatar": native["agency_avatar"],
            "creator_oec_id": oec_id, "partner_id": native["partner_id"], "tap_id": native["tap_id"], "cap_id": native["cap_id"]}}


@dataclass(frozen=True, repr=False)
class ItalyTextPacket:
    data: bytes
    sequence: int
    conversation_id: str
    full_cid: bytes
    conversation_type: int
    oec_id: str
    request_ref: str
    text: bytes
    sender_id: int
    market_id: str
    market: str
    market_region: str
    shop_region: str


def build_text_packet(auth: ItalyImAuthContext, conversation: VerifiedConversation, text: str, request_ref: str, *, sequence: int) -> ItalyTextPacket:
    """Pure cmd100/message_type1000 packet; does not invoke the legacy MX validator."""
    return _build_message_packet(auth, conversation, text, request_ref, sequence=sequence, extension={"type": "text", "original_content": ""})


def build_card_packet(auth: ItalyImAuthContext, conversation: VerifiedConversation, card: ItalyVerifiedProductCard,
                      request_ref: str, *, sequence: int) -> ItalyTextPacket:
    """The existing native product_list shape, with only own IT sender identity."""
    return _build_message_packet(auth, conversation, CARD_CONTENT, request_ref, sequence=sequence, extension=_card_extensions(card))


def _build_message_packet(auth, conversation, text, request_ref, *, sequence, extension):
    native = _native(auth);request_ref = _uuid(request_ref);content = _text(text)
    if not isinstance(conversation, VerifiedConversation) or type(sequence) is not int or not 1 <= sequence <= (1 << 63) - 1:
        raise ItalyImDeliveryError("it_delivery_input_invalid")
    cid, oec = _id(conversation.conversation_id), _id(conversation.oec_id)
    if not isinstance(conversation.full_cid, bytes) or not conversation.full_cid or len(conversation.full_cid) > 1024 or \
            not isinstance(conversation.ticket, bytes) or not conversation.ticket or len(conversation.ticket) > 65536 or \
            type(conversation.conversation_type) is not int or conversation.conversation_type != 2:
        raise ItalyImDeliveryError("it_delivery_input_invalid")
    wire = _proto()
    ext = {"PIGEON_BIZ_TYPE": "1", "sender_role": "4", "sender_im_role": "4", "sender_im_id": native["market_id"],
           "shop_region": native['shop_region'],
           "monitor_send_message_platform": "pc", **extension}
    body = (wire.vb(1, conversation.full_cid) + wire.vi(2, conversation.conversation_type) + wire.vi(3, int(cid)) + wire.vb(4, content)
            + b"".join(wire.vb(5, wire.vb(1, key) + wire.vb(2, value)) for key, value in ext.items())
            + wire.vi(6, 1000) + wire.vb(7, conversation.ticket) + wire.vb(8, request_ref))
    data = (wire.vi(1, 100) + wire.vi(2, sequence) + wire.vb(3, "1.2.2") + wire.vb(4, native["token"])
            + wire.vi(5, 3) + wire.vi(6, 0) + wire.vb(7, "5dd76f3:master") + wire.vb(8, wire.vb(100, body))
            + wire.vb(9, native["im_id"]) + wire.vb(11, "web") + wire.vi(18, 2))
    # Verify the exact locally constructed IT request rather than relaxing READ_HOST.
    envelope = wire.wire_fields(data);parsed = wire.wire_fields(wire.one(wire.wire_fields(wire.one(envelope, 8)), 100))
    if wire.one(envelope, 1) != 100 or wire.one(envelope, 2) != sequence or wire.one(envelope, 9) != native["im_id"].encode() or \
            wire.one(parsed, 1) != conversation.full_cid or wire.one(parsed, 3) != int(cid) or wire.one(parsed, 4) != content or wire.one(parsed, 6) != 1000 or wire.one(parsed, 8) != request_ref.encode():
        raise ItalyImDeliveryError("it_delivery_input_invalid")
    return ItalyTextPacket(data, sequence, cid, conversation.full_cid, conversation.conversation_type, oec,
                           request_ref, content, int(native["im_id"]), native["market_id"],native['market'],
                           native['market_region'],native['shop_region'])


def _ext(wire, message):
    result = {}
    for entry in message.get(9, []):
        pair = wire.wire_fields(entry);key, value = wire.one(pair, 1), wire.one(pair, 2)
        if not isinstance(key, bytes) or not isinstance(value, bytes) or key in result:
            raise ValueError("invalid extension")
        result[key] = value
    return result


def decode_send_candidate(data: bytes, packet: ItalyTextPacket) -> dict:
    wire = _proto()
    try:
        if not isinstance(data, bytes) or len(data) > 8_000_000:
            raise ValueError()
        decoded = wire.decode_envelope(data, cmd=100, sequence=packet.sequence)
        response = wire.wire_fields(wire.one(decoded, 100))
        client = wire.one(response, 4, b"")
        if not isinstance(client, bytes) or client and client != packet.request_ref.encode():
            raise ValueError()
        status, check = wire.one(response, 3, 0), wire.one(response, 5, 0)
        if type(status) is not int or type(check) is not int:
            raise ValueError()
        if status in (1, 2, 3, 4, 5):
            raise ItalyImDeliveryError("it_delivery_send_rejected", outcome="rejected", native_status=status, check_code=check, check_message=wire.one(response,6,b"").decode("utf-8",errors="replace")[:1000],response_ref="im-response:"+hashlib.sha256(data).hexdigest())
        message_id = wire.one(response, 1)
        if status != 0 or type(message_id) is not int or not 0 < message_id <= (1 << 63) - 1:
            raise ValueError()
        return {"conversationId": packet.conversation_id, "requestRef": packet.request_ref, "messageId": str(message_id),
                "evidenceRef": packet.market+"-im-send:" + hashlib.sha256(data).hexdigest(), "accepted": True}
    except ItalyImDeliveryError:
        raise
    except Exception:
        raw_message=wire.one(response,6,b"") if isinstance(locals().get('response'),dict) else b""
        raise ItalyImDeliveryError("it_delivery_receipt_mismatch", outcome="result_unknown",native_status=locals().get('status'),check_code=locals().get('check'),check_message=raw_message.decode('utf-8',errors='replace')[:1000] if isinstance(raw_message,bytes) else None,response_ref="im-response:"+hashlib.sha256(data).hexdigest()) from None


def verify_history_body(body: bytes, conversation: VerifiedConversation, *, sender_id: str, market_id: str,
                        text: str, request_ref: str, message_id: str | None = None, wire=None,
                        card: ItalyVerifiedProductCard | None = None,market_region='8',shop_region='IT') -> str | None:
    """Return an exact server ID, or None if absent. CID/OEC proof is supplied by the read session."""
    wire = wire or _proto();sender_id = _id(sender_id);market_id = _id(market_id);request_ref = _uuid(request_ref);content = _text(text)
    expected_message = int(_id(message_id)) if message_id is not None else None
    expected_ext = _card_extensions(card) if card is not None else None
    if expected_ext is not None and text != CARD_CONTENT:
        raise ItalyImDeliveryError("it_delivery_card_binding_invalid")
    try:
        parsed = wire.wire_fields(body);matched = set()
        for raw in parsed.get(1, []):
            message = wire.wire_fields(raw)
            if wire.one(message, 1) != conversation.full_cid or wire.one(message, 5) != int(conversation.conversation_id):
                raise ValueError()
            ext = _ext(wire, message)
            server_id, client_id = wire.one(message, 3), ext.get(b"s:client_message_id")
            if client_id != request_ref.encode() and (expected_message is None or server_id != expected_message):
                continue
            # Legacy messages can carry stale per-message region metadata. Validate
            # the exact target's identity; every row still has to belong to this CID.
            shops={shop_region.encode(),shop_region.lower().encode()}
            if ext.get(b"creator_oec_id") not in (None, conversation.oec_id.encode()) or ext.get(b"shop_region") not in ({None}|shops) or ext.get(b"market_region") not in (None, market_region.encode()):
                raise ValueError()
            if type(server_id) is not int or not 0 < server_id <= (1 << 63) - 1 or expected_message is not None and server_id != expected_message or \
                    client_id != request_ref.encode() or wire.one(message, 7) != int(sender_id) or wire.one(message, 6) != 1000 or wire.one(message, 8) != content or \
                    ext.get(b"s:visible") not in (None, b"") or ext.get(b"visibility_type") not in (None, b"", b"0") or \
                    (expected_ext is None and ext.get(b"type") not in (None, b"text")) or \
                    ext.get(b"sender_role") not in (None, b"4") or ext.get(b"sender_im_role") not in (None, b"4") or ext.get(b"sender_im_id") not in (None, market_id.encode()):
                raise ValueError()
            if expected_ext is not None and any(ext.get(key.encode()) != expected_ext[key].encode()
                    for key in ("type", "product_id", "list_id", "campaign_id", "starling_content_key")):
                raise ValueError()
            matched.add(server_id)
        if len(matched) > 1:
            raise ItalyImDeliveryError("it_delivery_ambiguous", outcome="result_unknown")
        return str(next(iter(matched))) if matched else None
    except ItalyImDeliveryError:
        raise
    except Exception:
        raise ItalyImDeliveryError("it_delivery_history_mismatch", outcome="result_unknown") from None


def verify_card_history_body(body: bytes, conversation: VerifiedConversation, card: ItalyVerifiedProductCard, *,
                             sender_id: str, market_id: str, request_ref: str, message_id: str | None = None, wire=None,
                             market_region='8',shop_region='IT') -> str | None:
    return verify_history_body(body, conversation, sender_id=sender_id, market_id=market_id, text=CARD_CONTENT,
                               request_ref=request_ref, message_id=message_id, wire=wire, card=card,
                               market_region=market_region,shop_region=shop_region)


class ItalyImDeliveryAdapter:
    def __init__(self, auth: ItalyImAuthContext, read_session: ItalyImReadSession, *, wall_time=time.time):
        self.auth, self.read_session, self.wall_time = auth, read_session, wall_time
        self.native = _native(auth);self.dispatched = set()
        if not isinstance(read_session, ItalyImReadSession) or read_session.im_id != self.native["im_id"] or read_session.token != self.native["token"]:
            raise ItalyImDeliveryError("it_delivery_session_mismatch")
        self._headers()

    def _headers(self):
        source = self.read_session.headers
        if not isinstance(source, dict) or any(not isinstance(key, str) or not isinstance(value, str) or any(c in key + value for c in "\r\n") for key, value in source.items()):
            raise ItalyImDeliveryError("it_delivery_session_mismatch")
        if any(key.lower() in {"cookie", "authorization", "proxy-authorization", "x-im-paas-token"} for key in source) or len({key.lower() for key in source}) != len(source):
            raise ItalyImDeliveryError("it_delivery_session_mismatch")
        result = {key: value for key, value in source.items() if key.lower() in {"user-agent", "accept-language"}}
        return {**result, "origin": self.native['partner_host'], "referer": self.native['partner_host'] + "/"}

    def _check(self):
        session = self.read_session
        if _native(self.auth) != self.native or session.im_id != self.native["im_id"] or session.token != self.native["token"]:
            raise ItalyImDeliveryError("it_delivery_session_mismatch")
        self._headers()
        if session.stopped():raise ItalyImDeliveryError("it_delivery_stopped")
        if session.maintenance_due():raise ItalyImDeliveryError("it_delivery_maintenance_due")
        if session.monotonic() >= session.expires_at:raise ItalyImDeliveryError("it_delivery_auth_expired")

    def _conversation(self, conversation):
        if not isinstance(conversation, VerifiedConversation) or conversation.conversation_type != 2 or self.read_session.verified.get(conversation.conversation_id) is not conversation:
            raise ItalyImDeliveryError("it_delivery_conversation_unverified")

    def _dispatch(self, scope, before_dispatch, *, headers, data=None, payload=None):
        session = self.read_session;key = (scope["stage"], scope["requestRef"])
        if not callable(before_dispatch):raise ItalyImDeliveryError("it_delivery_dispatch_not_allowed")
        if key in self.dispatched:raise ItalyImDeliveryError("it_delivery_duplicate_dispatch")
        self._check();session.sleep(max(0, session.next_request_at - session.monotonic()));self._check()
        if session.request_budget is not None:
            session.request_budget.acquire()
            self._check()
        try:
            decision = before_dispatch(dict(scope))
        except ImProbeDeadline:
            raise
        except Exception:
            raise ItalyImDeliveryError("it_delivery_dispatch_not_allowed") from None
        if not isinstance(decision, dict) or decision.get("dispatchAllowed") is not True or decision.get("requestRef") != scope["requestRef"] or decision.get("stage") != scope["stage"]:
            raise ItalyImDeliveryError("it_delivery_dispatch_not_allowed")
        if scope["stage"] == "send_message":
            required = ("componentKind",) + (("productId", "listId", "campaignId", "bindingSha256") if scope["componentKind"] == "card" else ())
            if any(decision.get(key) != scope[key] for key in required):
                raise ItalyImDeliveryError("it_delivery_dispatch_not_allowed")
        self._check()
        # This local pacer shares read-session cadence, not the runner's global quota.
        session.next_request_at = session.monotonic() + (0 if session.request_budget is not None else 1.0)
        self.dispatched.add(key)
        counter = "conversationCreateRequests" if scope["stage"] == "create_conversation" else "sendRequests"
        session.report[counter] = session.report.get(counter, 0) + 1
        path = CREATE_PATH if scope["stage"] == "create_conversation" else SEND_PATH
        try:
            response = session.session.post("https://" + self.native['im_host'] + path, headers=headers,
                **({"json": payload} if payload is not None else {"data": data}), timeout=(5, 15), allow_redirects=False)
            if type(response.status_code) is not int or response.status_code != 200 or getattr(response, "headers", {}).get("bdturing-verify"):
                raise ValueError()
            return response
        except ImProbeDeadline:
            raise ItalyImDeliveryError("it_delivery_wall_timeout", outcome="result_unknown") from None
        except Exception:
            raise ItalyImDeliveryError("it_delivery_create_unknown" if payload is not None else "it_delivery_send_unknown", outcome="result_unknown") from None

    def create_once(self, oec_id: str, request_ref: str, *, before_dispatch=None) -> dict:
        oec_id, request_ref = _id(oec_id), _uuid(request_ref)
        payload = build_create_payload(self.auth, oec_id, created_at_ms=int(self.wall_time() * 1000))
        headers = {**self._headers(), "content-type": "application/json", "x-im-paas-token": self.native["token"]}
        scope = {"stage": "create_conversation", "requestRef": request_ref, "account": self.native['account'], "market": self.native['market'], "oecId": oec_id,
                 "payloadSha256": hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
        response = self._dispatch(scope, before_dispatch, headers=headers, payload=payload)
        try:
            body = response.json()
            if not isinstance(body, dict) or type(body.get("code")) is not int or body["code"] != 0 or not isinstance(body.get("data"), dict):raise ValueError()
            cid = _native_id(body["data"].get("conversation_short_id"));is_new = body["data"].get("is_new")
            if is_new is not None and type(is_new) is not bool:raise ValueError()
            if body["data"].get("creator_oec_id") is not None and _native_id(body["data"]["creator_oec_id"]) != oec_id:raise ValueError()
            return {"conversationId": cid, "requestRef": request_ref, "isNew": is_new, "candidate": True,
                    "evidenceRef": self.native['market']+"-im-create:" + hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
        except ImProbeDeadline:
            raise ItalyImDeliveryError("it_delivery_wall_timeout", outcome="result_unknown") from None
        except Exception:
            raise ItalyImDeliveryError("it_delivery_create_unknown", outcome="result_unknown") from None

    def send_once(self, conversation: VerifiedConversation, text: str, request_ref: str, *, before_dispatch=None) -> dict:
        self._conversation(conversation);self._check()
        self.read_session.sequence += 1
        packet = build_text_packet(self.auth, conversation, text, request_ref, sequence=self.read_session.sequence)
        return self._send_packet(packet, before_dispatch, component_scope={"componentKind": "text"})

    def send_card_once(self, conversation: VerifiedConversation, card: ItalyVerifiedProductCard, request_ref: str, *, before_dispatch=None) -> dict:
        self._conversation(conversation);self._check();_verified_card(card)
        if card.market!=self.native['market'] or card.account_name!=self.native['account']:
            raise ItalyImDeliveryError('it_delivery_card_binding_invalid')
        self.read_session.sequence += 1
        packet = build_card_packet(self.auth, conversation, card, request_ref, sequence=self.read_session.sequence)
        return self._send_packet(packet, before_dispatch, component_scope={"componentKind": "card",
            "productId": card.product_id, "listId": card.list_id, "campaignId": card.campaign_id,
            "bindingSha256": card.binding_sha256, "evidenceSha256": card.evidence_sha256,
            "verifiedAt": card.verified_at, "origin": card.origin})

    def _send_packet(self, packet, before_dispatch, *, component_scope):
        scope = {"stage": "send_message", "requestRef": packet.request_ref, "account": self.native['account'], "market": self.native['market'], "oecId": packet.oec_id,
                 "conversationId": packet.conversation_id, "textSha256": hashlib.sha256(packet.text).hexdigest(), **component_scope}
        response = self._dispatch(scope, before_dispatch, headers={**self._headers(), "content-type": "application/x-protobuf"}, data=packet.data)
        try:
            data = response.content
            return decode_send_candidate(data, packet)
        except ImProbeDeadline:raise ItalyImDeliveryError("it_delivery_wall_timeout", outcome="result_unknown") from None
        except ItalyImDeliveryError:raise
        except Exception:raise ItalyImDeliveryError("it_delivery_send_unknown", outcome="result_unknown") from None

    def readback(self, conversation: VerifiedConversation, text: str, request_ref: str, *, message_id: str | None = None) -> dict:
        return self._readback(conversation, text, request_ref, message_id=message_id)

    def readback_card(self, conversation: VerifiedConversation, card: ItalyVerifiedProductCard, request_ref: str, *, message_id: str | None = None) -> dict:
        _verified_card(card)
        return self._readback(conversation, CARD_CONTENT, request_ref, message_id=message_id, card=card)

    def _readback(self, conversation, text, request_ref, *, message_id=None, card=None):
        self._conversation(conversation);self._check();request_ref = _uuid(request_ref);_text(text)
        if message_id is not None:_id(message_id)
        wire = self.read_session.wire
        request = (wire.vb(1, conversation.full_cid) + wire.vi(2, conversation.conversation_type) + wire.vi(3, int(conversation.conversation_id))
                   + wire.vi(4, 1) + wire.vi(5, 0) + wire.vi(6, 20))
        try:
            body, raw, sequence = self.read_session._read(301, request)
            # Independently retain correlation in the confirmation chain.
            if wire.one(wire.decode_envelope(raw, cmd=301, sequence=sequence), 301) != body:
                raise ItalyImDeliveryError("it_delivery_history_mismatch", outcome="result_unknown")
            found = verify_history_body(body, conversation, sender_id=self.native["im_id"], market_id=self.native["market_id"], text=text,
                                        request_ref=request_ref, message_id=message_id, wire=wire, card=card,
                                        market_region=self.native['market_region'],shop_region=self.native['shop_region'])
            return {"status": "confirmed" if found else "result_unknown", "conversationId": conversation.conversation_id, "requestRef": request_ref,
                    "messageId": found, "evidenceRef": self.native['market']+"-im-history:" + hashlib.sha256(raw).hexdigest(),
                    "reason": None if found else "it_delivery_history_not_found"}
        except (ItalyImReadError, ItalyImDeliveryError) as error:
            code = error.code if isinstance(error, ItalyImDeliveryError) else "it_delivery_history_unavailable"
            return {"status": "result_unknown", "conversationId": conversation.conversation_id, "requestRef": request_ref, "messageId": None, "evidenceRef": None, "reason": code}
        except ImProbeDeadline:
            raise ItalyImDeliveryError("it_delivery_wall_timeout", outcome="result_unknown") from None
        except Exception:
            return {"status": "result_unknown", "conversationId": conversation.conversation_id, "requestRef": request_ref,
                    "messageId": None, "evidenceRef": None, "reason": "it_delivery_history_unavailable"}
