from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from bdhub.imbase.transport import _JS_ENSURE_CONV, _JS_SEND_TEXT
from bdhub.hub.markets import get as get_market
from bdhub.send.transport_scripts import (
    _JS_CARD_SEARCH,
    _JS_RESOLVE_SERIAL,
    _JS_SEND_CARD,
)


@dataclass(frozen=True, slots=True)
class ComponentOutcome:
    status: str
    platform_message_id: str | None
    error_code: str | None
    transport: dict


@dataclass(frozen=True, slots=True)
class ConversationResolution:
    cid: str
    method: str
    transport: dict
    page: object | None = field(default=None, compare=False, repr=False)


class ConversationResolutionError(RuntimeError):
    def __init__(self, error_code: str, transport: dict) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.transport = dict(transport)


def _page_gone(error: Exception) -> bool:
    detail = str(error).casefold()
    return any(marker in detail for marker in (
        "target crashed", "page crashed", "target page, context or browser has been closed",
        "target closed",
    ))


class ComponentSender:
    def __init__(
        self,
        page,
        *,
        market: str,
        account_name: str | None = None,
        conversation_loader: Callable[[], int] | None = None,
        targeted_conversation_resolver: (
            Callable[[str], ConversationResolution] | None
        ) = None,
    ) -> None:
        self.page = page
        self.market = market
        self.account_name = account_name
        self.market_meta = get_market(market)
        self._conversation_loader = conversation_loader
        self._conversation_loader_ran = False
        self._targeted_conversation_resolver = targeted_conversation_resolver
        self._targeted_send_cid: str | None = None
        self._targeted_send_page = None

    def resolve_conversation(self, oec_id: str) -> ConversationResolution:
        clean_oec_id = str(oec_id or "").strip()
        if not clean_oec_id:
            raise ConversationResolutionError(
                "conversation_oec_required",
                {
                    "oec_id": clean_oec_id,
                    "classification": "invalid_oec_id",
                    "fallback_used": False,
                },
            )
        first_error: ConversationResolutionError | None = None
        try:
            return self._resolve_conversation_once(clean_oec_id)
        except ConversationResolutionError as exc:
            first_error = exc
        assert first_error is not None

        targeted_error: ConversationResolutionError | None = None
        if (
            first_error.error_code == "conversation_unresolved"
            and self._targeted_conversation_resolver is not None
        ):
            try:
                targeted = self._targeted_conversation_resolver(clean_oec_id)
            except ConversationResolutionError as exc:
                targeted_error = exc
                if exc.error_code == "auth_required":
                    transport = dict(exc.transport)
                    transport["fallback_used"] = True
                    transport["targeted_fallback_used"] = True
                    transport["initial"] = first_error.transport
                    raise ConversationResolutionError(
                        exc.error_code,
                        transport,
                    ) from exc
            else:
                self._remember_targeted_page(targeted)
                transport = dict(targeted.transport)
                transport["fallback_used"] = True
                transport["targeted_fallback_used"] = True
                transport["initial"] = first_error.transport
                return ConversationResolution(
                    cid=targeted.cid,
                    method=targeted.method,
                    transport=transport,
                    page=targeted.page,
                )

        if self._conversation_loader is None:
            if targeted_error is not None:
                transport = dict(targeted_error.transport)
                transport["fallback_used"] = True
                transport["targeted_fallback_used"] = True
                transport["initial"] = first_error.transport
                raise ConversationResolutionError(
                    targeted_error.error_code,
                    transport,
                ) from targeted_error
            raise first_error

        try:
            self._load_conversations_once()
        except Exception as exc:
            raise ConversationResolutionError(
                "conversation_fallback_failed",
                {
                    "oec_id": clean_oec_id,
                    "classification": "fallback_failed",
                    "fallback_used": True,
                    "detail": str(exc),
                    "initial": first_error.transport,
                    "targeted": (
                        targeted_error.transport
                        if targeted_error is not None
                        else None
                    ),
                },
            ) from exc
        try:
            resolution = self._resolve_conversation_once(clean_oec_id)
        except ConversationResolutionError as second_error:
            transport = dict(second_error.transport)
            transport["fallback_used"] = True
            transport["initial"] = first_error.transport
            if targeted_error is not None:
                transport["targeted_fallback_used"] = True
                transport["targeted"] = targeted_error.transport
            error_code = second_error.error_code
            if (
                error_code == "conversation_unresolved"
                and targeted_error is not None
                and str(
                    targeted_error.transport.get("classification") or ""
                ) == "targeted_context_unresolved"
            ):
                error_code = "conversation_unreachable"
            raise ConversationResolutionError(
                error_code,
                transport,
            ) from second_error
        transport = dict(resolution.transport)
        transport["fallback_used"] = True
        transport["initial"] = first_error.transport
        if targeted_error is not None:
            transport["targeted_fallback_used"] = True
            transport["targeted"] = targeted_error.transport
        return ConversationResolution(
            cid=resolution.cid,
            method=resolution.method,
            transport=transport,
        )

    def resolve_after_activation_failure(
        self,
        oec_id: str,
    ) -> ConversationResolution:
        """Use the native targeted route after known-CID activation failed.

        ``ensure_conversation`` already exhausted ``changeContact`` on the
        generic IM page. Repeating the same generic probe before the native
        creator-targeted route only adds another bounded wait and cannot make
        that known CID more reachable.
        """
        clean_oec_id = str(oec_id or "").strip()
        if not clean_oec_id:
            raise ConversationResolutionError(
                "conversation_oec_required",
                {
                    "method": "targeted_im_after_activation",
                    "classification": "invalid_oec_id",
                },
            )
        if self._targeted_conversation_resolver is None:
            return self.resolve_conversation(clean_oec_id)
        try:
            targeted = self._targeted_conversation_resolver(clean_oec_id)
        except ConversationResolutionError as exc:
            transport = dict(exc.transport)
            transport["targeted_fallback_used"] = True
            transport["generic_probe_skipped"] = True
            error_code = exc.error_code
            if str(transport.get("classification") or "") == (
                "targeted_context_unresolved"
            ):
                error_code = "conversation_unreachable"
            raise ConversationResolutionError(
                error_code,
                transport,
            ) from exc
        self._remember_targeted_page(targeted)
        transport = dict(targeted.transport)
        transport["targeted_fallback_used"] = True
        transport["generic_probe_skipped"] = True
        return ConversationResolution(
            cid=targeted.cid,
            method=targeted.method,
            transport=transport,
            page=targeted.page,
        )

    def _remember_targeted_page(
        self,
        resolution: ConversationResolution,
    ) -> None:
        if resolution.page is None:
            return
        self._targeted_send_cid = str(resolution.cid)
        self._targeted_send_page = resolution.page

    def _send_page(self, cid: str):
        if (
            self._targeted_send_page is not None
            and self._targeted_send_cid == str(cid)
        ):
            return self._targeted_send_page
        return self.page

    def _resolve_conversation_once(
        self,
        oec_id: str,
    ) -> ConversationResolution:
        try:
            response = self.page.evaluate(
                _JS_RESOLVE_SERIAL,
                {"oecs": [str(oec_id)], "probe": False},
            )
        except Exception as exc:
            raise ConversationResolutionError(
                "im_page_unavailable" if _page_gone(exc) else "conversation_transport_exception",
                {
                    "oec_id": oec_id,
                    "classification": "transport_exception",
                    "fallback_used": False,
                    "detail": str(exc),
                },
            ) from exc
        if not isinstance(response, dict):
            raise self._malformed_resolution(oec_id)
        results = response.get("results")
        if not isinstance(results, list) or not results:
            raise self._malformed_resolution(oec_id)
        first = results[0]
        if not isinstance(first, dict):
            raise self._malformed_resolution(oec_id)
        if not any(
            key in first
            for key in ("cid", "classification", "method", "change_contact")
        ):
            raise self._malformed_resolution(oec_id)

        transport = self._resolution_transport(oec_id, first)
        cid = str(first.get("cid") or "").strip()
        if cid:
            method = str(first.get("method") or "sdk_store").strip()
            return ConversationResolution(
                cid=cid,
                method=method,
                transport=transport,
            )

        classification = str(
            first.get("classification") or "conversation_unresolved"
        ).strip()
        error_code = {
            "business_rejected": "conversation_business_rejected",
            "change_contact_rejected": "conversation_change_contact_rejected",
            "change_contact_timeout": "conversation_change_contact_timeout",
            "sdk_unavailable": "conversation_sdk_unavailable",
        }.get(classification, "conversation_unresolved")
        raise ConversationResolutionError(error_code, transport)

    def ensure_conversation(
        self,
        *,
        cid: str,
        oec_id: str | None,
    ) -> dict:
        """Activate a known CID in the SDK before the first send.

        TikTok's ``sendMessage`` still requires the creator context to be
        activated even when PostgreSQL already knows the conversation ID.
        """
        payload = {
            "cid": str(cid),
            "oec": str(oec_id or ""),
            "attempts": 3,
        }

        def activate():
            try:
                return self._send_page(cid).evaluate(
                    _JS_ENSURE_CONV,
                    payload,
                )
            except Exception as exc:
                raise ConversationResolutionError(
                    "im_page_unavailable" if _page_gone(exc) else "conversation_activation_failed",
                    {
                        "method": "known_cid_activation",
                        "classification": "transport_exception",
                        "detail": f"{type(exc).__name__}: {exc}",
                    },
                ) from exc

        response = activate()
        if not isinstance(response, dict) or not response.get("ok"):
            raise ConversationResolutionError(
                "conversation_activation_failed",
                {
                    "method": "known_cid_activation",
                    "classification": str(
                        (response or {}).get("err")
                        if isinstance(response, dict)
                        else "malformed_response"
                    ),
                    "conversation_cache_loaded": (
                        self._conversation_loader_ran
                    ),
                },
            )
        return {
            "method": "known_cid_activation",
            "classification": "activated",
        }

    def _load_conversations_once(self):
        if self._conversation_loader is None:
            return None
        if self._conversation_loader_ran:
            return 0
        self._conversation_loader_ran = True
        return self._conversation_loader()

    @staticmethod
    def _malformed_resolution(oec_id: str) -> ConversationResolutionError:
        return ConversationResolutionError(
            "conversation_unresolved",
            {
                "oec_id": oec_id,
                "classification": "malformed_response",
                "fallback_used": False,
            },
        )

    @staticmethod
    def _resolution_transport(oec_id: str, source: dict) -> dict:
        transport = {"oec_id": oec_id}
        for key in (
            "method",
            "classification",
            "business_code",
            "change_contact",
            "network_observed",
            "elapsed_ms",
        ):
            if key in source:
                transport[key] = source[key]
        transport["fallback_used"] = False
        return transport

    def send_text(self, *, cid: str, handle: str, text: str) -> ComponentOutcome:
        try:
            response = self._send_page(cid).evaluate(
                _JS_SEND_TEXT,
                {
                    "cid": str(cid),
                    "text": str(text),
                    "handle": str(handle),
                    "dry": False,
                },
            )
        except Exception as exc:
            return ComponentOutcome(
                status="unknown",
                platform_message_id=None,
                error_code="transport_exception",
                transport={"detail": str(exc)},
            )
        if not isinstance(response, dict):
            return ComponentOutcome(
                status="unknown",
                platform_message_id=None,
                error_code="malformed_transport",
                transport={},
            )

        transport = self._transport_subset(
            response,
            "flight",
            "err",
            "send_timed_out",
            "signal",
        )
        server_id = self._clean_value(response.get("serverId"))
        if response.get("ok"):
            if server_id:
                return ComponentOutcome(
                    status="sent",
                    platform_message_id=server_id,
                    error_code=None,
                    transport=transport,
                )
            return ComponentOutcome(
                status="unknown",
                platform_message_id=None,
                error_code="missing_server_id",
                transport=transport,
            )
        if isinstance(response.get("flight"), int) and response["flight"] < 0:
            return ComponentOutcome(
                status="failed_terminal",
                platform_message_id=None,
                error_code="contact_limit",
                transport=transport,
            )
        # 只有未进入发送 API 的失败可以安全重试；超时、抛错和缺少回执
        # 都不能证明平台未接收。
        if str(response.get("err") or "").strip() != "no api":
            return ComponentOutcome(
                status="unknown",
                platform_message_id=None,
                error_code="text_send_result_unknown",
                transport=transport,
            )
        return ComponentOutcome(
            status="failed_retryable",
            platform_message_id=None,
            error_code="text_send_failed",
            transport=transport,
        )

    def send_card(self, *, cid: str, handle: str, pid: str) -> ComponentOutcome:
        lookup = self._lookup_card(pid)
        if isinstance(lookup, ComponentOutcome):
            return lookup
        card = {
            "product_id": str(pid),
            "list_id": lookup["list_id"],
            "campaign_id": lookup["campaign_id"],
            "campaign_name": lookup["campaign_name"],
            "list_name": lookup["list_name"],
            "handle": str(handle),
        }
        try:
            response = self._send_page(cid).evaluate(
                _JS_SEND_CARD,
                {"cid": str(cid), "card": card, "dry": False},
            )
        except Exception as exc:
            return ComponentOutcome(
                status="unknown",
                platform_message_id=None,
                error_code="transport_exception",
                transport={"detail": str(exc)},
            )
        if not isinstance(response, dict):
            return ComponentOutcome(
                status="unknown",
                platform_message_id=None,
                error_code="malformed_transport",
                transport={},
            )
        transport = self._transport_subset(
            response, "cid", "before", "after", "flight", "err",
        )
        server_id = self._clean_value(response.get("serverId"))
        before = response.get("before")
        after = response.get("after")
        if type(response.get("flight")) is int and response["flight"] < 0:
            return ComponentOutcome(
                status="failed_terminal",
                platform_message_id=None,
                error_code="contact_limit",
                transport=transport,
            )
        if response.get("ok"):
            if not self._is_growth(before, after):
                return ComponentOutcome(
                    status="unknown",
                    platform_message_id=server_id,
                    error_code="card_readback_missing",
                    transport=transport,
                )
            if server_id:
                return ComponentOutcome(
                    status="sent",
                    platform_message_id=server_id,
                    error_code=None,
                    transport=transport,
                )
            return ComponentOutcome(
                status="unknown",
                platform_message_id=None,
                error_code="missing_server_id",
                transport=transport,
            )
        if self._is_growth(before, after):
            return ComponentOutcome(
                status="unknown",
                platform_message_id=None,
                error_code="card_delivery_uncertain",
                transport=transport,
            )
        if str(response.get("err") or "").strip() != "no api":
            return ComponentOutcome(
                status="unknown",
                platform_message_id=None,
                error_code="card_send_result_unknown",
                transport=transport,
            )
        return ComponentOutcome(
            status="failed_retryable",
            platform_message_id=None,
            error_code="card_send_failed",
            transport=transport,
        )

    def _lookup_card(self, pid: str) -> dict | ComponentOutcome:
        try:
            from .taplink.service import binding_for_send
            binding = binding_for_send(self.market, self.account_name, str(pid)) if self.account_name else None
            expected = ({"listId": binding["list_id"], "sourceCampaignId": binding["source_campaign_id"],
                         "listName": binding["list_name"], "campaignId": binding["campaign_id"]} if binding else {})
            response = self.page.evaluate(
                _JS_CARD_SEARCH,
                {
                    "pid": str(pid),
                    "host": self.market_meta.host,
                    "partnerId": self.market_meta.im_market_partner_id,
                    "aid": self.market_meta.aid,
                    **expected,
                },
            )
        except ValueError as exc:
            return ComponentOutcome(status="failed_retryable", platform_message_id=None,
                                    error_code="product_card_preparation_required", transport={"detail": str(exc)})
        except Exception as exc:
            return ComponentOutcome(
                status="failed_retryable",
                platform_message_id=None,
                error_code="transport_exception",
                transport={"detail": str(exc)},
            )
        if not isinstance(response, dict):
            return ComponentOutcome(
                status="failed_retryable",
                platform_message_id=None,
                error_code="card_lookup_malformed",
                transport={},
            )
        if not response.get("ok"):
            detail = str(response.get("err") or "").strip()
            if not self._is_explicit_product_unavailable(detail):
                return ComponentOutcome(
                    status="failed_retryable",
                    platform_message_id=None,
                    error_code="card_lookup_failed",
                    transport={"detail": detail} if detail else {},
                )
            return ComponentOutcome(
                status="failed_terminal",
                platform_message_id=None,
                error_code="product_card_unavailable",
                transport={"detail": detail},
            )
        list_id = self._clean_value(response.get("list_id"))
        if not list_id:
            return ComponentOutcome(
                status="failed_retryable",
                platform_message_id=None,
                error_code="card_lookup_malformed",
                transport={},
            )
        from .taplink.cleanup import CleanupStore
        if CleanupStore().is_blocked(self.market, list_id):
            return ComponentOutcome(status="failed_terminal", platform_message_id=None,
                                    error_code="product_card_retired", transport={"detail": "该商品列表已进入清洗或已删除"})
        return {
            "list_id": list_id,
            "campaign_id": self._clean_value(response.get("campaign_id")) or "0",
            "campaign_name": self._clean_value(response.get("campaign_name")) or "",
            "list_name": self._clean_value(response.get("list_name")) or "",
        }

    @staticmethod
    def _clean_value(value) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _transport_subset(source: dict, *keys: str) -> dict:
        transport = {}
        for key in keys:
            if key in source and source[key] is not None:
                transport[key] = source[key]
        return transport

    @staticmethod
    def _is_growth(before, after) -> bool:
        return isinstance(before, int) and isinstance(after, int) and after > before

    @staticmethod
    def _is_explicit_product_unavailable(detail: str) -> bool:
        lowered = detail.casefold()
        if "pid unavailable" in lowered or "product unavailable" in lowered:
            return True
        return "pid" in lowered and (
            "无货盘" in lowered
            or "不可用" in lowered
            or "鏃犺揣" in lowered
        )


__all__ = [
    "ComponentOutcome",
    "ComponentSender",
    "ConversationResolution",
    "ConversationResolutionError",
]
