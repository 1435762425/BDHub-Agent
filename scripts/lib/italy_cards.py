"""Read current Freegrin card facts from the exact ACC6/IT list and membership.

No offer selection, creation, sample policy or minimum commission is applied.
The two GET contracts are copied from TapLinkTransport.card and
CleanupTransport.products; the common query is the legacy pure _params method.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

from lib.italy_im_auth import ImProbeDeadline

HOST = "https://partner.eu.tiktokshop.com"
PID = "1729779362302171335"
LIST_ID = "8650713863250615062"
CAMPAIGN_ID = "7683821192368981782"
CARD_PATH = "/api/v1/affiliate/partner/im/product_list/list"
MEMBERS_PATH = "/api/v1/affiliate/partner/campaign/product_list/products"
SAFE_CODES = frozenset({"card_scope_invalid", "card_identity_invalid", "card_maintenance_due", "card_stopped", "card_runtime_unavailable",
    "card_transport_error", "card_http_rejected", "card_redirect_rejected", "card_verification_required", "card_system_error",
    "card_response_invalid", "card_business_rejected", "card_list_incomplete", "card_list_page_limit", "card_members_incomplete",
    "card_members_changed", "card_members_duplicate", "card_members_page_limit", "card_endpoint_forbidden", "wall_timeout"})


class ItalyCardsError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in SAFE_CODES else "card_response_invalid"
        super().__init__(self.code)


def _numeric(value):
    if type(value) is int:
        value = str(value)
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{1,64}", value) else None


def _text(value):
    return value if isinstance(value, str) and len(value) <= 300 and not any(ord(c) < 32 for c in value) and not re.search(r"https?://", value, re.I) else None


def _decimal(value, *, maximum=None, integer=False, allow_negative=False):
    if value is None:
        return {"state": "missing", "raw": None, "value": None}
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return {"state": "invalid", "raw": None, "value": None}
    raw = str(value)
    if len(raw) > 80 or not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?" if allow_negative else r"[0-9]+(?:\.[0-9]+)?", raw):
        return {"state": "invalid", "raw": None, "value": None}
    try:
        number = Decimal(raw)
        if not number.is_finite() or maximum is not None and number > maximum or integer and number != number.to_integral_value():
            raise ValueError()
    except (InvalidOperation, ValueError):
        return {"state": "invalid", "raw": None, "value": None}
    return {"state": "observed", "raw": raw, "value": format(number, "f")}


def _commission(value):
    result = _decimal(value)
    return {"state": result["state"], "rawHundredthsOfPercent": result["raw"],
            "percent": format(Decimal(result["value"]) / 100, "f") if result["value"] is not None else None}


def _product_facts(product, observed_at, endpoint):
    product = product if isinstance(product, dict) else {}
    return {"pid": _numeric(product.get("product_id")), "campaignId": _numeric(product.get("campaign_id")),
            "productName": _text(product.get("product_name")), "stock": _decimal(product.get("stock"), integer=True, allow_negative=True),
            "productStatus": _numeric(product.get("product_status")),
            "isUnderGoverned": product.get("is_under_governed") if type(product.get("is_under_governed")) is bool else None,
            "unavailableType": _decimal(None if product.get("unavailable_type")=="" else product.get("unavailable_type")),
            "creatorCommission": _commission(product.get("creator_commission_percent")),
            "publicCommission": _commission(product.get("plan_commission_percent")),
            "totalCommission": _commission(product.get("total_commission_percent")),
            "source": {"endpointPath": endpoint, "observedAt": observed_at}}


def combine_card_facts(im_rows, members, *, im_observed_at, members_observed_at):
    """Join by expected list + PID + member Campaign, never by title or first row."""
    listing = [row for row in im_rows if isinstance(row, dict) and _numeric(row.get("product_list_id")) == LIST_ID]
    member_matches = [row for row in members if isinstance(row, dict) and _numeric(row.get("product_id")) == PID and _numeric(row.get("campaign_id")) == CAMPAIGN_ID]
    im_products = []
    if len(listing) == 1 and isinstance(listing[0].get("campaign_products"), list):
        im_products = [row for row in listing[0]["campaign_products"] if isinstance(row, dict) and _numeric(row.get("product_id")) == PID]
    list_row = listing[0] if len(listing) == 1 else None
    im_product = im_products[0] if len(im_products) == 1 else None
    member = member_matches[0] if len(member_matches) == 1 else None
    top_raw = list_row.get("campaign_id") if list_row else None
    top = _numeric(top_raw)
    item_raw = im_product.get("campaign_id") if im_product else None
    if im_product and item_raw in (None, "") and isinstance(im_product.get("campaign_info"), dict):
        item_raw = im_product["campaign_info"].get("campaign_id")
    item_campaign = _numeric(item_raw)
    checks = {"expectedListUnique": len(listing) == 1, "exactPidInImList": len(im_products) == 1,
              "exactPidCampaignInMembership": len(member_matches) == 1,
              "selectedListCampaignNotConflicting": top_raw in (None, "") or top == "0",
              "imProductCampaignNotConflicting": item_raw in (None, "") or item_campaign == CAMPAIGN_ID}
    binding_verified = all(checks.values())
    return {"schema": "bdhub.italy-card-facts.v1", "market": "it", "account": "acc6",
            "expected": {"pid": PID, "listId": LIST_ID, "campaignId": CAMPAIGN_ID, "source": 2},
            "status": "observed" if binding_verified else "binding_not_verified", "bindingVerified": binding_verified,
            "bindingEvidence": "im_list_and_product_list_membership" if binding_verified else None, "checks": checks,
            "card": {"pid": PID, "listId": LIST_ID, "imListCampaignId": top, "sourceCampaignId": CAMPAIGN_ID,
                     "listName": _text(list_row.get("product_list_name")) if list_row else None,
                     "campaignName": _text(list_row.get("campaign_name")) if list_row else None} if list_row else None,
            "imProduct": _product_facts(im_product, im_observed_at, CARD_PATH) if im_product else None,
            "membershipProduct": _product_facts(member, members_observed_at, MEMBERS_PATH) if member else None,
            "counts": {"matchingLists": len(listing), "matchingImProducts": len(im_products), "matchingMembers": len(member_matches)},
            "platformWrites": 0, "browserInitializations": 0, "sendRequests": 0,
            "scope": "Current read facts only; no minimum commission, sample policy or sending decision applied."}


def legacy_params(identity, account):
    """Reuse the current query builder without constructing transport or stores."""
    sys.dont_write_bytecode = True
    from lib.legacy_runtime import configure_vendored_bdhub
    configure_vendored_bdhub()
    from bdhub.send.sharelink.transport import ShareLinkBrowserTransport
    context = SimpleNamespace(identity=identity, fp=str(getattr(account, "fp", "") or ""), device_id=str(getattr(account, "device_id", "0") or "0"))
    return ShareLinkBrowserTransport._params(context)


def read_freegrin_card(account, identity, source_headers, report, *, http=None, params_factory=legacy_params,
                       maintenance_due=lambda: False, stopped=lambda: False, monotonic=time.monotonic, sleep=time.sleep,
                       wall_clock=lambda: datetime.now(timezone.utc).isoformat(), on_update=lambda: None):
    if getattr(account, "name", None) != "acc6" or getattr(account, "enabled", True) is False:
        raise ItalyCardsError("card_scope_invalid")
    if (getattr(identity, "market", None) != "it" or getattr(identity, "host", None) != HOST
            or str(getattr(identity, "aid", "")) != "360019" or getattr(identity, "partner_id_is_own", False) is not True
            or _numeric(getattr(identity, "im_market_partner_id", None)) is None):
        raise ItalyCardsError("card_identity_invalid")
    if not isinstance(source_headers, dict):
        raise ItalyCardsError("card_identity_invalid")
    headers, seen = {}, set()
    for key, value in source_headers.items():
        if not isinstance(key, str) or not isinstance(value, str) or key.lower() in seen or any(c in key + value for c in "\r\n"):
            raise ItalyCardsError("card_identity_invalid")
        seen.add(key.lower())
        if not key.startswith(":") and key.lower() not in {"host", "content-length", "origin", "referer", "connection"}:
            headers[key] = value
    if not any(key.lower() == "cookie" and value.strip() for key, value in headers.items()):
        raise ItalyCardsError("card_identity_invalid")
    headers.update(origin=HOST, referer=HOST + "/")
    base_params = params_factory(identity, account)
    if base_params.get("partner_id") != str(identity.im_market_partner_id) or base_params.get("aid") != "360019":
        raise ItalyCardsError("card_identity_invalid")
    session, owned, next_request_at = http, False, 0.0
    report.update(market="it", account="acc6", partnerApiHost="partner.eu.tiktokshop.com", requests=[],
                  expectedPid=PID, expectedListId=LIST_ID, expectedCampaignId=CAMPAIGN_ID,
                  environmentProxy=False, platformWrites=0, browserInitializations=0, sendRequests=0)
    try:
        if session is None:
            try:
                import requests
                session=requests.Session();owned=True;session.trust_env=False;session.auth=lambda request:request
                session.mount("https://",requests.adapters.HTTPAdapter(max_retries=0))
            except Exception:
                raise ItalyCardsError("card_runtime_unavailable") from None

        def read(path, additions):
            nonlocal next_request_at
            if path not in {CARD_PATH, MEMBERS_PATH}:
                raise ItalyCardsError("card_endpoint_forbidden")
            if stopped(): raise ItalyCardsError("card_stopped")
            if maintenance_due(): raise ItalyCardsError("card_maintenance_due")
            sleep(max(0, next_request_at-monotonic()))
            if stopped(): raise ItalyCardsError("card_stopped")
            if maintenance_due(): raise ItalyCardsError("card_maintenance_due")
            start=monotonic();next_request_at=start+1
            entry={"endpointPath":path,"method":"GET","status":"inflight","httpStatus":None,"code":None,"latencyMs":None,"observedAt":None}
            report["requests"].append(entry);on_update()
            try:
                response=session.get(HOST+path,headers=headers,params=base_params|additions,timeout=(5,15),allow_redirects=False)
                code=getattr(response,"status_code",None);entry.update(status="returned",httpStatus=code if type(code) is int else None)
                if getattr(response,"headers",{}).get("bdturing-verify"):raise ItalyCardsError("card_verification_required")
                if getattr(response,"headers",{}).get("x-tt-system-error")=="3":raise ItalyCardsError("card_system_error")
                if code!=200 or type(code) is not int:raise ItalyCardsError("card_redirect_rejected" if type(code) is int and 300<=code<400 else "card_http_rejected")
                try:payload=response.json()
                except Exception:raise ItalyCardsError("card_response_invalid") from None
                if not isinstance(payload,dict):raise ItalyCardsError("card_response_invalid")
                code=payload.get("code");entry["code"]=code if type(code) is int and -1_000_000_000<=code<=1_000_000_000 else None
                if type(code) is not int or code!=0:raise ItalyCardsError("card_verification_required" if code in (10000,"10000") else "card_business_rejected")
                entry['observedAt']=wall_clock()
                return payload,entry['observedAt']
            except ImProbeDeadline:
                entry.update(status="error",errorCode="wall_timeout");raise
            except ItalyCardsError as error:
                entry["errorCode"]=error.code;raise
            except Exception:
                entry.update(status="error",errorCode="card_transport_error");raise ItalyCardsError("card_transport_error") from None
            finally:
                entry['latencyMs']=round(max(0,monotonic()-start)*1000,1)
                if entry['observedAt'] is None:entry['observedAt']=wall_clock()
                on_update()

        im_rows=[];im_time=None
        for page in range(1,21):
            payload,im_time=read(CARD_PATH,{"cur_page":page,"page_size":20,"version":1,"search_type":2,"key_word":PID})
            data=payload.get("data")
            if not isinstance(data,dict):raise ItalyCardsError("card_list_incomplete")
            rows=data.get("list")
            if type(data.get("total")) is int and data["total"]==0 and rows is None:rows=[]
            if not isinstance(rows,list):raise ItalyCardsError("card_list_incomplete")
            im_rows.extend(row for row in rows if isinstance(row,dict) and _numeric(row.get("product_list_id"))==LIST_ID)
            if im_rows or len(rows)<20:break
        else:raise ItalyCardsError("card_list_page_limit")
        members=[];seen=set();cursors=set();cursor=None;total=None;members_time=None;target_member_time=None
        for _ in range(101):
            query={"list_id":LIST_ID,"source":2}
            if cursor is not None:query["cursor"]=cursor
            payload,members_time=read(MEMBERS_PATH,query);data=payload.get("data")
            if not isinstance(data,dict) or type(data.get("total_num")) is not int or data["total_num"]<0:raise ItalyCardsError("card_members_incomplete")
            if total is not None and total!=data["total_num"]:raise ItalyCardsError("card_members_changed")
            total=data["total_num"];rows=data.get("campaign_products",[])
            if not isinstance(rows,list):raise ItalyCardsError("card_members_incomplete")
            for row in rows:
                if not isinstance(row,dict):raise ItalyCardsError("card_members_incomplete")
                key=(_numeric(row.get("product_id")),_numeric(row.get("campaign_id")))
                if key[0] is None or key in seen:raise ItalyCardsError("card_members_duplicate")
                seen.add(key);members.append(row)
                if key==(PID,CAMPAIGN_ID):target_member_time=members_time
            if len(members)==total:break
            cursor=data.get("next_cursor")
            if not rows or not cursor or not isinstance(cursor,(str,int)) or isinstance(cursor,bool) or str(cursor) in cursors or len(members)>total:raise ItalyCardsError("card_members_incomplete")
            cursors.add(str(cursor))
        else:raise ItalyCardsError("card_members_page_limit")
        result=combine_card_facts(im_rows,members,im_observed_at=im_time,members_observed_at=target_member_time or members_time)
        report.update(bindingVerified=result["bindingVerified"],resultStatus=result["status"],checks=result["checks"])
        return result
    finally:
        if owned and session is not None:session.close()
