"""Canonical current TapLink binding consumed by leads and sending.

Preparation/readback tables remain immutable evidence.  This projection answers the one operational
question they should not answer by accident: which exact standard card is current for this offer?
"""
import json
from contextlib import closing
from decimal import Decimal,InvalidOperation
from pathlib import Path
import sqlite3
import time

from lib.second_cycle import digest, encoded

ACTIVE = "active"
STATES = {ACTIVE, "waiting_refresh", "inactive"}


def _text(value, code):
    if not isinstance(value, str) or not value or len(value) > 500 or "\x00" in value:
        raise ValueError(code)
    return value


def _digits(value, code):
    text = _text(str(value), code)
    if not text.isascii() or not text.isdigit():
        raise ValueError(code)
    return text


def offer_fingerprint(offer):
    if not isinstance(offer, dict):
        raise ValueError("catalog_binding_offer_invalid")
    pinned = offer.get("planFingerprint")
    if isinstance(pinned, str) and len(pinned) == 64:
        return pinned
    keys = ("pid", "campaignId", "catalogSource", "creatorPercent", "publicPercent", "totalPercent")
    if any(offer.get(key) in (None, "") for key in keys):
        raise ValueError("catalog_binding_offer_invalid")
    return digest({key: offer.get(key) for key in keys})


class CatalogBindings:
    def __init__(self, root, *, connection=None):
        self.root = Path(root)
        self._owns = connection is None
        self.db = connection or sqlite3.connect(self.root / "var/catalog-links.sqlite", timeout=15,
                                                isolation_level=None)
        self.db.row_factory = sqlite3.Row
        required = {"catalog_current_binding", "catalog_current_binding_event"}
        found = {row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)", tuple(required))}
        if found != required:
            if self._owns:
                self.db.close()
            raise ValueError("catalog_schema_migration_required")

    def close(self):
        if self._owns:
            self.db.close()

    def get(self, market, source, pid, campaign_id):
        row = self.db.execute("SELECT * FROM catalog_current_binding WHERE market=? AND catalog_source=? "
                              "AND pid=? AND campaign_id=?",
                              (market, source, str(pid), str(campaign_id))).fetchone()
        if not row:
            return None
        return dict(row) | {"card": json.loads(row["card_payload"])}

    def promote(self, spec, card, intent_id=None, *, now=None):
        if not isinstance(spec, dict) or not isinstance(card, dict):
            raise ValueError("catalog_binding_invalid")
        market = _text(spec.get("market"), "catalog_binding_market_invalid")
        source = _text(spec.get("route"), "catalog_binding_source_invalid")
        if source not in ("selected", "campaign"):
            raise ValueError("catalog_binding_source_invalid")
        pid = _digits(spec.get("pid"), "catalog_binding_pid_invalid")
        campaign = _digits(spec.get("campaignId"), "catalog_binding_campaign_invalid")
        list_id = _digits(card.get("listId"), "catalog_binding_list_invalid")
        creator = _text(str(spec.get("creatorPercent")), "catalog_binding_commission_invalid")
        name = _text(spec.get("listName"), "catalog_binding_name_invalid")
        commission_version = _text(spec.get("policyVersion"), "catalog_binding_policy_invalid")
        naming_version = _text(spec.get("namingVersion"), "catalog_binding_naming_invalid")
        if commission_version != "commission-1-to-2-v1" or naming_version != "link-naming-v1":
            raise ValueError("catalog_binding_rule_version_invalid")
        expected = {"state": "verified_read_only", "pid": pid, "sourceCampaignId": campaign,
                    "creatorPercent": creator, "verifiedListName": name, "listId": list_id}
        if any(str(card.get(key)) != str(value) for key, value in expected.items()):
            raise ValueError("catalog_binding_card_mismatch")
        checked = card.get("checkedAt")
        if isinstance(checked, bool) or not isinstance(checked, (int, float)) or checked <= 0:
            raise ValueError("catalog_binding_checked_at_invalid")
        stamp = time.time() if now is None else now
        fingerprint = offer_fingerprint(spec.get("offer"))
        payload = {"market": market, "catalogSource": source, "pid": pid,
                   "campaignId": campaign, "offerFingerprint": fingerprint,
                   "listId": list_id, "commissionRuleVersion": commission_version,
                   "namingRuleVersion": naming_version, "creatorPercent": creator,
                   "listName": name, "card": card, "linkIntentId": intent_id,
                   "state": ACTIVE, "verifiedAt": float(checked)}
        event_id = "catalog-binding-" + digest(payload)[:28]
        old = self.get(market, source, pid, campaign)
        if old and old["verified_at"] > float(checked):
            if old["list_id"]==list_id and old["offer_fingerprint"]==fingerprint and \
                    old["creator_percent"]==creator and old["list_name"]==name:
                return old
            raise ValueError("catalog_binding_stale_observation")
        def write():
            self.db.execute("INSERT OR IGNORE INTO catalog_current_binding_event VALUES(?,?,?,?,?,?,?,?)",
                            (event_id, market, source, pid, campaign, ACTIVE, encoded(payload), stamp))
            self.db.execute("""INSERT INTO catalog_current_binding(
                market,catalog_source,pid,campaign_id,offer_fingerprint,list_id,
                commission_rule_version,naming_rule_version,creator_percent,list_name,card_payload,
                link_intent_id,state,verified_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(market,catalog_source,pid,campaign_id) DO UPDATE SET
                offer_fingerprint=excluded.offer_fingerprint,list_id=excluded.list_id,
                commission_rule_version=excluded.commission_rule_version,
                naming_rule_version=excluded.naming_rule_version,
                creator_percent=excluded.creator_percent,list_name=excluded.list_name,
                card_payload=excluded.card_payload,link_intent_id=excluded.link_intent_id,
                state=excluded.state,verified_at=excluded.verified_at,updated_at=excluded.updated_at""",
                (market, source, pid, campaign, fingerprint, list_id, commission_version,
                 naming_version, creator, name, encoded(card), intent_id, ACTIVE, float(checked), stamp))
        if self.db.in_transaction:
            write()
        else:
            with self.db:
                write()
        return self.get(market, source, pid, campaign)

    def active_for_offer(self, offer, *, market="it"):
        if not isinstance(offer, dict):
            return None
        row = self.get(market, offer.get("catalogSource"), offer.get("pid"), offer.get("campaignId"))
        if not row or row["state"] != ACTIVE:
            return None
        if row["offer_fingerprint"] != offer_fingerprint(offer):
            return None
        if row["commission_rule_version"] != "commission-1-to-2-v1" or \
                row["naming_rule_version"] != "link-naming-v1":
            return None
        return row

    def active_pids(self, market="it"):
        return {str(row[0]) for row in self.db.execute(
            "SELECT DISTINCT pid FROM catalog_current_binding WHERE market=? AND state=?",
            (market, ACTIVE))}

    def mark_waiting_refresh(self, offer, *, reason, evidence_ref=None, now=None, market="it"):
        """Remove one explicitly rejected material from the sendable projection, with audit evidence."""
        row = self.active_for_offer(offer, market=market)
        if not row:
            return None
        stamp = time.time() if now is None else now
        payload = {"market": market, "catalogSource": row["catalog_source"], "pid": row["pid"],
                   "campaignId": row["campaign_id"], "listId": row["list_id"],
                   "state": "waiting_refresh", "reason": _text(reason, "catalog_binding_reason_invalid"),
                   "evidenceRef": evidence_ref}
        event_id = "catalog-binding-" + digest(payload)[:28]
        def write():
            self.db.execute("INSERT OR IGNORE INTO catalog_current_binding_event VALUES(?,?,?,?,?,?,?,?)",
                            (event_id, market, row["catalog_source"], row["pid"], row["campaign_id"],
                             "waiting_refresh", encoded(payload), stamp))
            self.db.execute("UPDATE catalog_current_binding SET state='waiting_refresh',updated_at=? "
                            "WHERE market=? AND catalog_source=? AND pid=? AND campaign_id=? "
                            "AND list_id=? AND offer_fingerprint=?",
                            (stamp, market, row["catalog_source"], row["pid"], row["campaign_id"],
                             row["list_id"], row["offer_fingerprint"]))
        if self.db.in_transaction:
            write()
        else:
            with self.db:
                write()
        return self.get(market, row["catalog_source"], row["pid"], row["campaign_id"])


def audit_existing(root, *, apply=False, now=None):
    """Recognize provably exact standard cards from verified local intents; never calls a platform."""
    root=Path(root);path=root/"var/catalog-links.sqlite"
    if not path.exists():raise ValueError("catalog_binding_source_missing")
    policy=json.loads((root/"config/catalog-link-policy.json").read_text(encoding="utf-8"))
    from lib.catalog_links import new_commission
    from lib.link_naming import fingerprint as naming_fingerprint,load as load_naming,name_for
    naming=load_naming(root);report={"mode":"apply" if apply else "check","scanned":0,"qualifying":0,
                                    "promoted":0,"reasons":{},"platformWrites":0}
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)) as db:
        db.row_factory=sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_link_intent'").fetchone():return report
        rows=list(db.execute("SELECT id,spec,readback FROM catalog_link_intent WHERE state='verified' ORDER BY created,id"))
    bindings=CatalogBindings(root) if apply else None
    try:
        for row in rows:
            report["scanned"]+=1
            try:
                spec=json.loads(row["spec"]);card=json.loads(row["readback"])
                if spec.get("purpose")!="catalog_batch_link":raise ValueError("not_batch_standard")
                offer=dict(spec.get("offer") or {});offer.setdefault("catalogSource",spec.get("route"))
                total=Decimal(str(offer["totalPercent"]));public=Decimal(str(offer["publicPercent"]))
                total_raw=int(total if total>100 else total*100);public_raw=int(public if public>100 else public*100)
                expected=new_commission(total_raw,public_raw,policy)
                if expected["creatorPercent"]!=str(spec.get("creatorPercent")):raise ValueError("commission_rule_mismatch")
                rendered=name_for(root,pid=str(spec["pid"]),campaign=str(spec["campaignId"]),
                                  creator_percent=str(spec["creatorPercent"]),short_name=str(spec["shortName"]),
                                  public_percent=offer.get("publicPercent"),total_percent=offer.get("totalPercent"),
                                  market=str(spec.get("market") or "it"),config=naming)
                if rendered["name"]!=spec.get("listName"):raise ValueError("naming_rule_mismatch")
                normalized=spec|{"policyVersion":policy["version"],"namingVersion":naming["version"],
                                 "namingFingerprint":naming_fingerprint(naming),"offer":offer}
                # Promotion performs the final exact card/name/rate/binding validation.
                if apply:bindings.promote(normalized,card,row["id"],now=now)
                else:
                    checked=card.get("checkedAt")
                    if isinstance(checked,bool) or not isinstance(checked,(int,float)) or checked<=0:raise ValueError("catalog_binding_checked_at_invalid")
                    if any(str(card.get(k))!=str(v) for k,v in {"state":"verified_read_only","pid":str(spec["pid"]),
                      "sourceCampaignId":str(spec["campaignId"]),"creatorPercent":str(spec["creatorPercent"]),
                      "verifiedListName":spec["listName"]}.items()):raise ValueError("catalog_binding_card_mismatch")
                report["qualifying"]+=1;report["promoted"]+=int(apply)
            except (KeyError,TypeError,ValueError,InvalidOperation) as error:
                reason=str(error) if isinstance(error,ValueError) and str(error) else type(error).__name__
                report["reasons"][reason]=report["reasons"].get(reason,0)+1
    finally:
        if bindings:bindings.close()
    return report
