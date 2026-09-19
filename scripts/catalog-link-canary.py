#!/usr/bin/env python3
"""Prepare/execute/verify one ACC9 catalog link; no creator collection or messages."""
import argparse,fcntl,importlib.util,json,sys,time
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.catalog_links import CatalogLinks,new_commission
from lib.global_selection import selected_rows,assess
from lib.global_source import clean_product
from lib.global_source_transport import opportunity_reader,opportunity_card_creator,CREATE
from lib.cycle_catalog import normalize
from lib.second_cycle import digest,assess_offer
spec=importlib.util.spec_from_file_location('catalog_card_inspection',ROOT/'scripts/prepare-cycle-materials.py');inspection=importlib.util.module_from_spec(spec);spec.loader.exec_module(inspection)

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','execute','verify']);p.add_argument('--pid');p.add_argument('--short-name');p.add_argument('--id');p.add_argument('--reader-account',choices=['acc6','acc9'],default='acc9');p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    output=a.report.resolve()
    if not output.is_relative_to(ROOT/'var') or output.exists():p.error('new report under var required')
    with (ROOT/'var/cycle-card-create.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);ledger=CatalogLinks(ROOT);report={'action':a.action,'realSends':0,'platformWrites':0,'startedAt':time.time()}
        def save():output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        row=None
        try:
            if a.action=='prepare':
                if not a.pid or not a.pid.isdigit() or len(a.pid)!=19 or not a.short_name or not 1<=len(a.short_name)<=30:p.error('PID and short name required')
                pid=a.pid;payload=None
            else:
                row=ledger.get(a.id);pid=row['pid'];report['intentId']=row['id']
                if row['state']=='verified' and a.action=='execute':report.update(state='already_verified',card=row['readback']);save();return
                payload=row['spec']['payload']
            manager=opportunity_card_creator(report,payload) if a.action=='execute' else opportunity_reader(report,account_name=a.reader_account if a.action=='verify' else 'acc9',wait_seconds=60 if a.action=='verify' else 15,extra_read_endpoints={(inspection.CARD,'GET'),(inspection.MEMBERS,'GET')})
            with manager as t:
                def read(path,extra):
                    r=t._xhr(method='GET',path=path,params=t._params()|extra,payload=None,write=False);body=t.require_read(r);return body,digest(body)
                def search():
                    body,_=read(inspection.CARD,{'cur_page':1,'page_size':20,'version':1,'search_type':2,'key_word':pid});data=body.get('data') or {};total=data.get('total')
                    if type(total) is not int or total<0:raise ValueError('card_search_incomplete')
                    return total
                def fresh_offer():
                    listing=t.opportunity_page(1,global_only=True,pids=[pid]);matches=[p for p in listing['products'] if str(p.get('product_id'))==pid]
                    if listing['has_more'] or len(matches)!=1 or not assess(clean_product(matches[0]))['eligible']:raise ValueError('product_no_longer_eligible')
                    offers=[];rule=ledger.policy|{'id':ledger.policy['version']}
                    def calc(total,public):
                        q=new_commission(total,public,ledger.policy);return SimpleNamespace(valid=True,creator_pct=Decimal(q['creatorPercent']))
                    for raw in selected_rows(t,[pid]):
                        if str(raw.get('campaign_info',{}).get('crs_campaign_type')) not in ('8','9'):continue
                        try:o=normalize(raw['campaign_product'],raw['campaign_info'],'selected',rule,calc,digest(raw),time.time())
                        except ValueError:continue
                        if assess_offer(o,time.time())['eligible']:offers.append(o)
                    if not offers:raise ValueError('no_eligible_selected_offer')
                    return max(offers,key=lambda o:(Decimal(o['creatorPercent']),o['endAt'],o['campaignId']))
                if a.action=='prepare':
                    offer=fresh_offer();total=search();report.update(pid=pid,offer=offer,existingCardCount=total)
                    if total:report['state']='existing_links_preserved_requires_reuse_review';save();return
                    from bdhub.send.taplink.protocol import create_payload
                    name=f"BJN {a.short_name} {offer['creatorPercent']}% "+digest([pid,offer['campaignId'],offer['creatorPercent']])[:6]
                    payload=create_payload(pid=pid,campaign_id=offer['campaignId'],creator_pct=offer['creatorPercent'],name=name,route='selected')
                    from lib.catalog_links import policy_fingerprint
                    planned={'market':'it','account':'acc9','route':'selected','purpose':'acc9_single_card_canary','pid':pid,'campaignId':offer['campaignId'],'creatorPercent':offer['creatorPercent'],'listName':name,'shortName':a.short_name,'policyFingerprint':policy_fingerprint(ledger.policy),'searchTotal':0,'offer':offer,'payload':payload,'preparedAt':time.time()}
                    row=ledger.prepare(planned);report.update(intentId=row['id'],state=row['state'],planned=planned)
                else:
                    planned=row['spec'];offer=planned['offer']
                    if a.action=='execute':
                        if row['state']!='prepared':raise ValueError('verify_existing_attempt_first')
                        if planned['policyFingerprint']!=policy_fingerprint(ledger.policy):raise ValueError('policy_changed')
                        fresh=fresh_offer()
                        if any(fresh.get(k)!=offer.get(k) for k in ('campaignId','creatorPercent','totalPercent','publicPercent')):raise ValueError('commercial_facts_changed')
                        if search()!=0:raise ValueError('existing_links_preserved_no_creation')
                        if time.time()-fresh['observedAt']>60:raise ValueError('preflight_expired')
                        ledger.begin(row['id'],'acc9');report['state']='submitted';save()
                        r=t._xhr(method='POST',path=CREATE,params=t._params(),payload=payload,write=True)
                        report['nativeReceipt']={'http':r.http_status,'code':r.code if type(r.code) is int else None,'verification':r.has_turing,'ambiguous':r.ambiguous}
                        if r.has_turing and getattr(t,'_verification_header',''):
                            t._solve_verification(t._verification_header);t.verification_successes+=1;report['verificationResolved']=True
                            ledger.unknown(row['id'],'verification_required_readback_pending')
                        else:
                            from bdhub.send.taplink.protocol import creation_receipt
                            body=t.require_read(r);receipt=creation_receipt(body);receipt['responseHash']=digest(body);ledger.receipt(row['id'],receipt)
                    current=ledger.get(row['id']);receipt=current['receipt'] or {};card=None
                    for delay in (0,1,3):
                        if delay:time.sleep(delay)
                        card=inspection.inspect_card(offer,read,expected_list_id=receipt.get('list_id'),expected_name=planned['listName'])
                        if card['state']=='verified_read_only':break
                    if not card or card['state']!='verified_read_only':raise ValueError('created_card_not_verified')
                    card.update(readAccount=report['scope']['account']);ledger.confirm(row['id'],card);report.update(state='verified',card=card)
        except Exception as e:
            code=str(e) if isinstance(e,ValueError) else type(e).__name__
            if row:ledger.unknown(row['id'],code)
            report.update(state='verification_failed' if a.action=='verify' else ledger.get(row['id'])['state'] if row else 'blocked',error=code)
        finally:
            report['elapsedSeconds']=round(time.time()-report['startedAt'],2);save();ledger.db.close()
        print(json.dumps(report,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
