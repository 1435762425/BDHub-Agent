"""Bind the verified ACC6/IT Freegrin read facts to an IM card.

The caller owns the account guard and refresh deadline. These helpers neither
acquire leases nor open legacy stores. Observations are evidence, not a promise
that the recipient's commission has already changed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from lib.italy_cards import CARD_PATH, MEMBERS_PATH, PID, LIST_ID, CAMPAIGN_ID, read_freegrin_card
from lib.italy_im_delivery import CARD_ORIGIN, CARD_TITLE_KEY, ItalyVerifiedProductCard, card_binding_sha256

VAR_ROOT = Path(__file__).resolve().parents[2] / "var"
MAX_FACTS_BYTES = 128 * 1024
CHECKS = frozenset({"expectedListUnique", "exactPidInImList", "exactPidCampaignInMembership",
                    "selectedListCampaignNotConflicting", "imProductCampaignNotConflicting"})
SAFE_CODES = frozenset({"card_facts_invalid", "card_facts_scope_mismatch", "card_facts_unverified",
                        "card_stock_unavailable", "card_binding_changed", "card_facts_path_invalid",
                        "card_facts_file_invalid", "card_under_governance", "card_unavailable", "card_stock_invalid", "promotion_condition_changed"})


class SecondCardBindingError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in SAFE_CODES else "card_facts_invalid"
        super().__init__(self.code)


def validate_promotion_claims(claims, commercial):
    """Validate a fixed message's benefit against this current card observation."""
    if not claims:return
    try:
        for claim in claims:
            if claim['kind']!='commission_above_public' or claim['pid']!=commercial['productId']:
                raise ValueError()
            sources=commercial['sources'];member=sources['membership']
            creator,public=member['creatorCommission'],member['publicCommission']
            if creator['state']!='observed' or public['state']!='observed':raise ValueError()
            creator_value,public_value=Decimal(creator['percent']),Decimal(public['percent'])
            if not creator_value.is_finite() or not public_value.is_finite() or not creator_value>public_value:raise ValueError()
            im_creator=sources['im']['creatorCommission']
            if im_creator['state']=='observed' and not Decimal(im_creator['percent'])>public_value:raise ValueError()
    except (KeyError,TypeError,ValueError,InvalidOperation):
        raise SecondCardBindingError('promotion_condition_changed') from None


def _invalid():
    raise SecondCardBindingError("card_facts_invalid")


def _time(value):
    try:
        if not isinstance(value, str):
            _invalid()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            _invalid()
        return parsed
    except (TypeError, ValueError):
        _invalid()


def _name(value, *, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or len(value) > 1000 or "\x00" in value:
        _invalid()
    try:
        value.encode("utf-8")
    except UnicodeError:
        _invalid()


def _number(value, *, integer=False, negative=False):
    if not isinstance(value, str) or len(value) > 80 or re.fullmatch(
        r"-?[0-9]+(?:\.[0-9]+)?" if negative else r"[0-9]+(?:\.[0-9]+)?", value
    ) is None:
        _invalid()
    try:
        number = Decimal(value)
        if not number.is_finite() or integer and number != number.to_integral_value():
            _invalid()
        return number
    except InvalidOperation:
        _invalid()


def _numeric_fact(value, *, commission=False, stock=False):
    raw_key, value_key = ("rawHundredthsOfPercent", "percent") if commission else ("raw", "value")
    if not isinstance(value, dict) or set(value) != {"state", raw_key, value_key}:
        _invalid()
    if not isinstance(value["state"], str):
        _invalid()
    if value["state"] in {"missing", "invalid"}:
        if value[raw_key] is not None or value[value_key] is not None:
            _invalid()
        return None
    if value["state"] != "observed":
        _invalid()
    raw = _number(value[raw_key], integer=stock, negative=stock)
    number = _number(value[value_key], integer=stock, negative=stock)
    if number != (raw / 100 if commission else raw):
        _invalid()
    return number


def _validated_facts(facts):
    if not isinstance(facts, dict) or facts.get("schema") != "bdhub.italy-card-facts.v1":
        _invalid()
    if (facts.get("market") != "it" or facts.get("account") != "acc6"
            or facts.get("expected") != {"pid": PID, "listId": LIST_ID, "campaignId": CAMPAIGN_ID, "source": 2}
            or type(facts["expected"]["source"]) is not int):
        raise SecondCardBindingError("card_facts_scope_mismatch")
    checks, counts = facts.get("checks"), facts.get("counts")
    if (facts.get("status") != "observed" or facts.get("bindingVerified") is not True
            or facts.get("bindingEvidence") != "im_list_and_product_list_membership"
            or not isinstance(checks, dict) or set(checks) != CHECKS
            or not all(value is True for value in checks.values())
            or not isinstance(counts, dict) or set(counts) != {"matchingLists", "matchingImProducts", "matchingMembers"}
            or not all(type(value) is int and value == 1 for value in counts.values())):
        raise SecondCardBindingError("card_facts_unverified")
    if any(type(facts.get(key)) is not int or facts[key] != 0
           for key in ("platformWrites", "browserInitializations", "sendRequests")):
        _invalid()
    card = facts.get("card")
    if (not isinstance(card, dict) or not {"pid", "listId", "imListCampaignId", "sourceCampaignId", "listName", "campaignName"}.issubset(card)
            or card.get("pid") != PID or card.get("listId") != LIST_ID
            or card.get("imListCampaignId") != "0" or card.get("sourceCampaignId") != CAMPAIGN_ID):
        raise SecondCardBindingError("card_facts_scope_mismatch")
    _name(card.get("listName")); _name(card.get("campaignName"), nullable=True)
    for key, endpoint in (("imProduct", CARD_PATH), ("membershipProduct", MEMBERS_PATH)):
        product = facts.get(key)
        if (not isinstance(product, dict) or product.get("pid") != PID
                or product.get("campaignId") not in ((None, CAMPAIGN_ID) if key == "imProduct" else (CAMPAIGN_ID,))):
            raise SecondCardBindingError("card_facts_scope_mismatch")
        if not {"pid", "campaignId", "productName", "stock", "productStatus", "isUnderGoverned", "unavailableType",
                "creatorCommission", "publicCommission", "totalCommission", "source"}.issubset(product):
            _invalid()
        _name(product.get("productName"), nullable=True)
        status = product.get("productStatus")
        if status is not None and (not isinstance(status, str) or re.fullmatch(r"[0-9]{1,64}", status) is None):
            _invalid()
        if product.get("isUnderGoverned") is not None and type(product["isUnderGoverned"]) is not bool:
            _invalid()
        _numeric_fact(product.get("stock"), stock=True)
        _numeric_fact(product.get("unavailableType"))
        for rate in ("creatorCommission", "publicCommission", "totalCommission"):
            _numeric_fact(product.get(rate), commission=True)
        source = product.get("source")
        if not isinstance(source, dict) or source.get("endpointPath") != endpoint:
            _invalid()
        _time(source.get("observedAt"))
    try:
        # Also reject non-JSON numbers such as NaN before hashing the evidence.
        json.dumps(facts, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _invalid()
    return facts


def _evidence_sha256(facts):
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _stock_blocks(facts):
    return any(stock is not None and stock <= 0 for stock in
        (_numeric_fact(facts[key]["stock"], stock=True) for key in ("imProduct", "membershipProduct")))


def card_from_facts(facts) -> ItalyVerifiedProductCard:
    """Make a descriptor only from the exact joined read proof; known zero stock blocks."""
    facts = _validated_facts(facts)
    for key in ("imProduct", "membershipProduct"):
        product=facts[key]
        if product["stock"]["state"]=="invalid":
            raise SecondCardBindingError("card_stock_invalid")
        # Same exclusions as the proven MX _JS_CARD_SEARCH implementation.
        if product["isUnderGoverned"] is True:
            raise SecondCardBindingError("card_under_governance")
        unavailable=product["unavailableType"]
        if unavailable["state"]=="invalid" or unavailable["state"]=="observed" and _numeric_fact(unavailable)!=0:
            raise SecondCardBindingError("card_unavailable")
    if _stock_blocks(facts):
        raise SecondCardBindingError("card_stock_unavailable")
    source = facts["card"]
    observed = [facts[key]["source"]["observedAt"] for key in ("imProduct", "membershipProduct")]
    fields = {"product_id": PID, "list_id": LIST_ID, "campaign_id": source["imListCampaignId"],
              "campaign_name": source["campaignName"] or "", "list_name": source["listName"],
              "market": "it", "account_name": "acc6", "title_key": CARD_TITLE_KEY}
    return ItalyVerifiedProductCard(**fields, verified_at=max(observed, key=_time), origin=CARD_ORIGIN,
        evidence_sha256=_evidence_sha256(facts), binding_sha256=card_binding_sha256(fields), verified=True)


def commercial_facts_from_facts(facts) -> dict:
    """UI projection of both sources, retaining unknowns and source Campaign separately.

    This remains available for out-of-stock facts so the UI can explain a block.
    It contains no recipient-level commission activation or sample entitlement.
    """
    facts = _validated_facts(facts)
    keys = ("productName", "stock", "productStatus", "isUnderGoverned", "unavailableType",
            "creatorCommission", "publicCommission", "totalCommission", "source")
    return {"schema": "bdhub.italy-card-commercial-facts.v1", "market": "it", "account": "acc6",
            "productId": PID, "listId": LIST_ID, "imWireCampaignId": facts["card"]["imListCampaignId"],
            "sourceCampaignId": CAMPAIGN_ID, "listName": facts["card"]["listName"],
            "campaignName": facts["card"]["campaignName"], "evidenceSha256": _evidence_sha256(facts),
            "knownStockBlocksCard": _stock_blocks(facts),
            "sources": {label: {key: deepcopy(facts[source][key]) for key in keys}
                        for label, source in (("im", "imProduct"), ("membership", "membershipProduct"))}}


def load_card_facts(path, *, var_root=VAR_ROOT) -> dict:
    """Read a bounded local facts file within the new project's var directory."""
    try:
        root = Path(var_root).resolve(strict=True)
        candidate = Path(path)
        candidate = candidate if candidate.is_absolute() else root / candidate
        candidate = candidate.resolve(strict=True)
        if candidate == root or not candidate.is_relative_to(root):
            raise SecondCardBindingError("card_facts_path_invalid")
        descriptor = os.open(candidate, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise SecondCardBindingError("card_facts_file_invalid")
            data = handle.read(MAX_FACTS_BYTES + 1)
        if len(data) > MAX_FACTS_BYTES:
            raise SecondCardBindingError("card_facts_file_invalid")
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result
        facts = json.loads(data, object_pairs_hook=unique_object)
    except SecondCardBindingError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise SecondCardBindingError("card_facts_file_invalid") from None
    return deepcopy(_validated_facts(facts))


def refresh_card_binding(previous: ItalyVerifiedProductCard, account, identity, source_headers, report,
                         *, collector=read_freegrin_card, **collector_options):
    """Refresh under the caller's guard; timestamps/rates may change, card binding may not.

    Return (descriptor, commercial facts, collected evidence). All deadline,
    maintenance and cancellation callbacks pass directly to the read collector.
    No retry, extra guard, legacy store, write, or commission activation is added.
    """
    if not isinstance(previous, ItalyVerifiedProductCard) or previous.binding_sha256 != card_binding_sha256(previous):
        raise SecondCardBindingError("card_facts_invalid")
    facts = collector(account, identity, source_headers, report, **collector_options)
    current = card_from_facts(facts)
    if current.binding_sha256 != previous.binding_sha256:
        raise SecondCardBindingError("card_binding_changed")
    return current, commercial_facts_from_facts(facts), deepcopy(facts)
