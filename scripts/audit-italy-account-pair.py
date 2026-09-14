#!/usr/bin/env python3
"""Read-only ACC6/ACC9 identity, catalog and shared-card audit. Never sends or refreshes credentials."""
import argparse,hashlib,importlib.util,json,sys,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];LEGACY=ROOT.parent/'01-BDSystem-V2'
sys.dont_write_bytecode=True;sys.path.insert(0,str(LEGACY));sys.path.insert(0,str(ROOT/'scripts'))
from bdhub import scheduled_relogin
from bdhub.hub.markets import identity_for
from bdhub.send.taplink.transport import account_for
from bdhub.research.commerce_transport import CommerceTransport
from lib.second_cycle import digest
INFO='/api/v1/affiliate/partner/info';IMID='/api/v1/affiliate/partner/im/id/get';TOKEN='/api/v1/affiliate/partner/im/token/get'
SELECTED='/api/v1/affiliate/partner/product/pick_up/list';CARD='/api/v1/affiliate/partner/im/product_list/list'
PID='1729779362302171335'
spec=importlib.util.spec_from_file_location('pair_guard',ROOT/'scripts/probe-italy-profile.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)

class Reader(CommerceTransport):
    READ_ENDPOINTS=frozenset({(INFO,'GET'),(IMID,'GET'),(TOKEN,'GET'),(SELECTED,'POST'),(CARD,'GET')})
    WRITE_ENDPOINTS=frozenset()

def audit(name):
    report={'account':name,'market':'it','checks':[],'platformWrites':0,'messagesSent':0,'startedAt':time.time()}
    cfg,account=account_for('it',name,check_maintenance=False);identity=identity_for('it',account=account,cfg=cfg).require_product_search()
    if not identity.partner_id_is_own:raise ValueError('not_own_institution')
    path=Path(account.headers_json);before=hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with guard.readonly_guard(account,wait_seconds=15):
            report['guardAcquiredAt']=time.time();t=Reader(identity,account,allow_write=False)
            def stop_check():
                if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):raise ValueError('maintenance_due')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=before:raise ValueError('identity_changed')
            t.check_stop=stop_check
            def read(label,method,endpoint,params,payload=None,auth=False):
                t.session.trust_env=auth;at=time.monotonic()
                r=t._xhr(method=method,path=endpoint,params=params,payload=payload,write=False)
                report['checks'].append({'check':label,'http':r.http_status,'code':r.code if type(r.code) is int else None,'seconds':round(time.monotonic()-at,3),'verificationRequired':r.has_turing})
                body=t.require_read(r);return body.get('data',body)
            try:
                params={'aid':identity.aid,'partner_id':str(identity.partner_id)}
                info=read('institution_market','GET',INFO,params|{'partner_type':1},auth=True)
                markets=(info.get('partner_biz_role_info') or {}).get('market_list',[])
                matches=[m for m in markets if str(m.get('market_region'))=='8' and any(str(r.get('partner_id'))==str(identity.partner_id) for r in m.get('type_list',[]))]
                if len(matches)!=1:raise ValueError('market_institution_not_verified')
                market_id=str(matches[0].get('market_id') or '')
                if not market_id.isdigit():raise ValueError('market_id_missing')
                report['institutionFingerprint']=digest(str(identity.im_market_partner_id));report['partnerFingerprint']=digest(str(identity.partner_id));report['marketFingerprint']=digest(market_id)
                im=read('im_identity','GET',IMID,params|{'user_id':market_id,'type':0},auth=True);im_id=str(im.get('im_id') or '')
                if not im_id.isdigit():raise ValueError('im_id_missing')
                token=read('im_token','GET',TOKEN,params|{'im_id':im_id},auth=True)
                if not token.get('token') or token.get('im_id') is not None and str(token['im_id'])!=im_id:raise ValueError('token_identity_invalid')
                report['senderFingerprint']=digest(im_id);report['tokenPresent']=True
                report['tokenExpiryFields']={k:token[k] for k in ('expire_time','expires_in') if type(token.get(k)) in (int,float)}
                t.session.trust_env=False
                rows=t.selected_page(1,pids=[PID]);report['checks'].append({'check':'selected_product','rows':len(rows['items']),'total':rows['total']})
                if not rows['items'] or any(str(r.get('campaign_product',{}).get('product_id'))!=PID for r in rows['items']):raise ValueError('selected_product_not_visible')
                data=read('im_card_search','GET',CARD,t._params()|{'cur_page':1,'page_size':20,'version':1,'search_type':2,'key_word':PID})
                if type(data.get('total')) is not int or data['total']<0:raise ValueError('card_search_shape')
                cards=data.get('list',[]) if data['total']==0 else data.get('list')
                if not isinstance(cards,list):raise ValueError('card_search_shape')
                report['visibleCardIds']=sorted({str(c['product_list_id']) for c in cards if any(str(p.get('product_id'))==PID for p in c.get('campaign_products',[]))})
                report['state']='passed_readonly';report['capabilities']={'institution_market':'verified','catalog_read':'verified','im_token':'verified','im_card_search':'verified','message_send':'not_tested','link_create':'not_tested','product_select':'not_tested'}
            finally:
                t.session.close();report['verificationAttempts']=t.verification_attempts;report['guardReleasedAt']=time.time()
    except Exception as e:report.update(state='blocked',reason=str(e) if isinstance(e,ValueError) else type(e).__name__)
    finally:report.update(identityFileUnchanged=hashlib.sha256(path.read_bytes()).hexdigest()==before,elapsedSeconds=round(time.time()-report['startedAt'],3))
    return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);a=p.parse_args();out=a.report.resolve()
    if not out.is_relative_to(ROOT/'var') or out.exists():p.error('new report under project var required')
    with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(audit,['acc6','acc9']))
    passed=all(r['state']=='passed_readonly' and r['identityFileUnchanged'] for r in rows)
    report={'accounts':rows,'passed':passed,'scope':'readonly pair check, no message sends or authentication changes','platformWrites':0}
    if passed:
        report.update(sameInstitution=rows[0]['institutionFingerprint']==rows[1]['institutionFingerprint'],sameMarket=rows[0]['marketFingerprint']==rows[1]['marketFingerprint'],sameSender=rows[0]['senderFingerprint']==rows[1]['senderFingerprint'],sharedCardIds=sorted(set(rows[0]['visibleCardIds'])&set(rows[1]['visibleCardIds'])),independentGuardsOverlapSeconds=round(max(0,min(r['guardReleasedAt'] for r in rows)-max(r['guardAcquiredAt'] for r in rows)),3))
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
