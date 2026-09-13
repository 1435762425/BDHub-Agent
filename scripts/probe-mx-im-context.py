#!/usr/bin/env python3
"""Read known MX relationships via bundle-observed IM context contracts. No sends."""
import sys,json,hashlib,importlib.util,time
from pathlib import Path
from datetime import datetime,timezone
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1];LEGACY=ROOT.parent/'01-BDSystem-V2'
sys.path.insert(0,str(LEGACY));sys.path.insert(0,str(ROOT/'scripts'))
import requests
from sqlalchemy import text
from bdhub import config,scheduled_relogin
from bdhub.hub.engine import make_engine
from bdhub.hub.markets import identity_for
from bdhub.enrich.identity_store import load_identity
from bdhub.send.worker import _select_account
from lib.italy_cards import legacy_params
spec=importlib.util.spec_from_file_location('field_survey',ROOT/'scripts/probe-tiktok-field-shapes.py');survey=importlib.util.module_from_spec(spec);spec.loader.exec_module(survey)
spec=importlib.util.spec_from_file_location('im_guard',ROOT/'scripts/probe-italy-profile.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)

def main(out):
 if not out.resolve().is_relative_to(ROOT/'var') or out.exists():raise ValueError('new_var_file_required')
 cfg=config.load();account=_select_account(cfg,'acc1');identity=identity_for('mx',account=account,cfg=cfg).require_im()
 if not identity.partner_id_is_own:raise ValueError('identity_not_own')
 report={'schema':'bdhub.mx-im-context-probe.v1','observedAt':datetime.now(timezone.utc).isoformat(),'market':'mx','account':'acc1','platformWrites':0,'legacyWrites':0,'requests':[]}
 before=hashlib.sha256(Path(account.headers_json).read_bytes()).hexdigest()
 eng=make_engine(cfg,connect_timeout_seconds=5)
 with eng.connect() as c:
  with c.begin():
   c.execute(text('SET TRANSACTION READ ONLY'));c.execute(text("SET LOCAL statement_timeout='15000ms'"))
   rows=list(c.execute(text("SELECT conversation_id,creator_oec_id,max(create_ms) t FROM im_message WHERE bd_market='mx' AND sender_role=3 AND is_from_me=false AND content ~* 'vitrina|escaparate' AND creator_oec_id IS NOT NULL GROUP BY conversation_id,creator_oec_id ORDER BY t DESC LIMIT 3")).mappings())
 eng.dispose()
 try:
  with guard.readonly_guard(account):
   if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):raise ValueError('maintenance_due')
   headers={k:v for k,v in load_identity(account.headers_json).headers.items() if not k.startswith(':') and k.lower() not in ('host','content-length','origin','referer')};headers.update(origin='https://partner.tiktokshop.com',referer='https://partner.tiktokshop.com/')
   params=legacy_params(identity,account)
   calls=[('GET','/api/v1/affiliate/partner/im/collaboration/get',{'creator_id':r['creator_oec_id']},None) for r in rows]
   calls.append(('POST','/api/v1/affiliate/partner/im/filter/creator/mget',{}, {'creator_ids':[{'creator_oec_id':r['creator_oec_id'],'conversation_id':r['conversation_id']} for r in rows]}))
   with requests.Session() as session:
    session.trust_env=False
    for method,path,q,body in calls:
     time.sleep(1);item={'path':path,'method':method,'queryKeys':sorted(params|q),'bodyKeys':sorted(body or {}),'observedAt':datetime.now(timezone.utc).isoformat()};report['requests'].append(item)
     r=session.request(method,identity.host+path,params=params|q,json=body,headers=headers,timeout=(5,20),allow_redirects=False)
     data=r.json();item.update(http=r.status_code,businessCode=data.get('code'),verificationRequired=bool(r.headers.get('bdturing-verify')),responseSha256=hashlib.sha256(r.content).hexdigest())
     item['status']='observed' if r.status_code==200 and data.get('code')==0 and not item['verificationRequired'] else 'blocked'
     if item['status']=='observed':
      item['fields']=survey.field_shapes(data)
      item['permissionObservation']={k:v for k,v in data.items() if k in ('has_permission','collaboration_type')}
     else:break
 except Exception as e:report['errorType']=type(e).__name__
 finally:
  report['identityFileUnchanged']=hashlib.sha256(Path(account.headers_json).read_bytes()).hexdigest()==before
  out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'requests':[{k:r.get(k) for k in ('path','status','businessCode','permissionObservation')} for r in report['requests']],'errorType':report.get('errorType')}))
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args();main(args.output)
