#!/usr/bin/env python3
"""Local controls for account identity generations and maintenance intents."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.account_identity import assignments,promote_capabilities, request_maintenance, set_enabled, status  # noqa:E402
from lib.second_cycle import CycleError, CycleStore  # noqa:E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "set-enabled", "request","promote-canary"))
    parser.add_argument("--json")
    args = parser.parse_args()
    try:
        body = json.loads(args.json or "{}")
        market = body.get("market")
        if market is None and body.get("account"):
            matches = [row["market"] for row in assignments(ROOT)
                       if row["account"] == body.get("account")]
            market = matches[0] if len(matches) == 1 else None
        with CycleStore(ROOT / "var/second-cycle.sqlite") as store:
            if args.action == "set-enabled":
                set_enabled(store, ROOT, market, body.get("account"), body.get("enabled"),
                            body.get("requestId"), body.get("expectedRevision"))
            elif args.action == "request":
                request_maintenance(store, ROOT, market=market, account=body.get("account"),
                                    operation=body.get("operation"), request_id=body.get("requestId"))
            elif args.action == 'promote-canary':
                if set(body)!={'market','account','capabilities','evidenceRef'}:raise CycleError('identity_capability_promotion_invalid')
                evidence=(ROOT/str(body['evidenceRef'])).resolve()
                if not evidence.is_relative_to((ROOT/'var').resolve()) or not evidence.exists():raise CycleError('identity_capability_promotion_invalid')
                proof=json.loads(evidence.read_text(encoding='utf-8'));capabilities=set(body.get('capabilities') or [])
                nested=sum(int(((row.get('result') or {}).get('platformWrites') or 0)) for row in proof.get('steps',[]) if isinstance(row,dict)) if isinstance(proof,dict) else 0
                writes=max(int(proof.get('platformWrites') or 0),nested) if isinstance(proof,dict) else 0
                if 'product_select' in capabilities and (writes<1 or not (proof.get('states') or {}).get('confirmed')):raise CycleError('identity_capability_evidence_invalid')
                if 'taplink' in capabilities and (writes<1 or not any(int(((row.get('result') or {}).get('created') or 0))>0 for row in proof.get('steps',[]) if isinstance(row,dict))):raise CycleError('identity_capability_evidence_invalid')
                if capabilities&{'oecid_find','creator_profile'} and (proof.get('status')!='completed' or proof.get('market')!=market or proof.get('account')!=body.get('account') or proof.get('identityFileUnchanged') is not True):raise CycleError('identity_capability_evidence_invalid')
                if 'creator_profile' in capabilities and not any(row.get('status')=='completed' for row in proof.get('targets',[]) if isinstance(row,dict)):raise CycleError('identity_capability_evidence_invalid')
                if 'message_send' in capabilities and (proof.get('state')!='confirmed' or proof.get('realSends')!=1 or proof.get('unknown')!=0):raise CycleError('identity_capability_evidence_invalid')
                promote_capabilities(store,market=market,account=body.get('account'),capabilities=body.get('capabilities'),evidence_ref=str(evidence.relative_to(ROOT)))
            result = status(store, ROOT)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (CycleError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
