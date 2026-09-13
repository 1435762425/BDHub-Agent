#!/usr/bin/env python3
"""Bounded read-only MX/IT/BR field survey; stores paths/types, never raw payloads."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
LEGACY=ROOT.parent/'01-BDSystem-V2'
READS={
 'campaigns':('GET','/api/v1/affiliate/partner/campaign/list'),
 'selected':('POST','/api/v1/affiliate/partner/product/pick_up/list'),
 'samples':('POST','/api/v1/affiliate/partner/sample/records/list'),
 'members':('GET','/api/v1/affiliate/partner/campaign/product_list/products'),
}

def field_shapes(value):
    result={}
    def walk(v,path):
        t='null' if v is None else 'boolean' if isinstance(v,bool) else 'object' if isinstance(v,dict) else 'array' if isinstance(v,list) else 'number' if isinstance(v,(int,float)) else 'string'
        row=result.setdefault(path,Counter());row[t]+=1
        if isinstance(v,dict):
            for k,x in v.items():walk(x,path+'.'+k)
        elif isinstance(v,list):
            for x in v:walk(x,path+'[]')
    walk(value,'$')
    return {k:dict(v) for k,v in sorted(result.items())}

def run(output, markets=('mx','it','br'), only=None):
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(LEGACY))
    sys.path.insert(0,str(ROOT/'scripts'))
    import requests
    from bdhub import scheduled_relogin
    from bdhub.enrich.identity_store import load_identity
    from bdhub.hub.markets import identity_for
    from bdhub.send.taplink.transport import account_for
    from lib.italy_cards import legacy_params
    spec=importlib.util.spec_from_file_location('survey_guard',ROOT/'scripts/probe-italy-profile.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    result={'schema':'bdhub.tiktok.field-survey.v1','observedAt':datetime.now(timezone.utc).isoformat(),'platformWrites':0,'legacyWrites':0,'requests':[],'rawResponsesStored':False}
    output.parent.mkdir(parents=True,exist_ok=True)
    def save():
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for market in markets:
        before=None;path=None
        try:
            cfg,account=account_for(market,'acc6',check_maintenance=False)
            identity=identity_for(market,account=account,cfg=cfg).require_product_search()
            if not identity.partner_id_is_own:raise ValueError('identity_not_own')
            path=Path(account.headers_json);before=hashlib.sha256(path.read_bytes()).hexdigest()
            with mod.readonly_guard(account):
                if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):raise ValueError('maintenance_due')
                headers={k:v for k,v in load_identity(path).headers.items() if not k.startswith(':') and k.lower() not in ('host','content-length','origin','referer')}
                from urllib.parse import urlsplit
                h=urlsplit(identity.home);origin=h.scheme+'://'+h.netloc
                headers.update(origin=origin,referer=origin+'/')
                params=legacy_params(identity,account)
                with requests.Session() as session:
                    session.trust_env=False
                    names=[only] if only else ['campaigns','selected']+(['samples'] if market=='mx' else ['members'] if market=='it' else [])
                    if 'members' in names and market!='it':raise ValueError('members_scope_is_it_only')
                    for name in names:
                        method,endpoint=READS[name]
                        query=dict(params);body=None
                        if name=='campaigns':query.update(campaign_join_status_category='1',crs_campaign_types='',cur_page=1,page_size=10)
                        elif name=='selected':body={'cur_page':1,'page_size':5,'filter':{'product_source':[],'campaign_type':[],'label_type':[],'product_status':1}}
                        elif name=='samples':body={'search_params':[{'search_key':28,'search_type':1,'value':'2'}],'order_params':[{'order_key':9,'order_type':2}],'page_size':5,'cur_page':1}
                        elif name=='members':query.update(list_id='8650713863250615062',source=2)
                        time.sleep(1)
                        item={'market':market,'account':'acc6','name':name,'method':method,'path':endpoint,'observedAt':datetime.now(timezone.utc).isoformat(),'scope':'one page, not full dataset','queryKeys':sorted(query),'bodyKeys':sorted(body or {})}
                        result['requests'].append(item);save()
                        try:
                            r=session.request(method,identity.host+endpoint,params=query,json=body,headers=headers,timeout=(5,20),allow_redirects=False)
                            item.update(http=r.status_code,verificationRequired=bool(r.headers.get('bdturing-verify')),systemError=r.headers.get('x-tt-system-error')=='3')
                            data=r.json();item['businessCode']=data.get('code') if isinstance(data,dict) and isinstance(data.get('code'),int) else None
                            item['responseSha256']=hashlib.sha256(r.content).hexdigest()
                            item['status']='observed' if r.status_code==200 and item['businessCode']==0 and not item['verificationRequired'] and not item['systemError'] else 'blocked'
                            if item['status']=='observed':item['fields']=field_shapes(data)
                        except Exception as e:item.update(status='failed',errorType=type(e).__name__)
                        save()
                        if item['status']!='observed':break
        except Exception as e:
            result.setdefault('scopeErrors',[]).append({'market':market,'errorType':type(e).__name__})
        finally:
            if before:result.setdefault('identityChecks',[]).append({'market':market,'unchanged':hashlib.sha256(path.read_bytes()).hexdigest()==before})
            save()
    print(json.dumps({'requests':[{k:r.get(k) for k in ('market','name','status','http','businessCode')} for r in result['requests']], 'scopeErrors':result.get('scopeErrors',[]),'fieldPaths':sum(len(r.get('fields',{})) for r in result['requests'])}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--markets',nargs='+',choices=['mx','it','br'],default=['mx','it','br'])
    p.add_argument('--only',choices=list(READS));args=p.parse_args()
    out=args.output.resolve()
    if not out.is_relative_to(ROOT/'var'):p.error('output must be in new project var')
    if out.exists():p.error('use a new evidence file')
    if len(set(args.markets))!=len(args.markets):p.error('duplicate market')
    run(out,args.markets,args.only)
