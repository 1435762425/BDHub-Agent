#!/usr/bin/env python3
"""Extract historical SDK wrapper definitions without executing JavaScript."""
import argparse,hashlib,json,re
from pathlib import Path

def extract(path):
 raw=path.read_bytes();s=raw.decode('utf-8');rows={};known_get='_t="GET"' in s
 for m in re.finditer(r't\.(\w+)=function\(e,t\)\{',s):
  chunk=s[m.end():m.end()+650]
  endpoint=re.search(r'this\.uriPrefix\+"/api/v"\+\(e\.version\|\|"1"\)\+"([^\"]+)"',chunk)
  if not endpoint or endpoint.start()>=160:continue
  p='/api/v1'+endpoint.group(1)
  method=re.search(r'method:([^,}]+)',chunk)
  token=method.group(1) if method else ''
  resolved=token.strip('"') if token in ('"GET"','"POST"','"PUT"','"DELETE"') else 'GET' if token=='_t' and known_get else 'unverified_alias'
  ref={'wrapper':m.group(1),'offset':m.start(),'methodExpression':token,'method':resolved}
  row=rows.setdefault(p,{'path':p,'defaultVersion':1,'evidence':'historical_sdk_definition','runtimeAvailable':False,'sideEffect':'unverified','references':[]})
  row['references'].append(ref)
 result=[]
 for i,(p,row) in enumerate(sorted(rows.items()),1):
  row['id']=f'JS-{i:03}'
  row['roleScope']='creator_side_candidate' if '/partner/creator/' in p else 'notification_or_seller_candidate' if '/notification/' in p else 'partner_candidate' if '/partner/' in p else 'other_shared_sdk'
  result.append(row)
 return {'schema':'bdhub.tiktok.web-bundle-inventory.v1','sourcePath':str(path.resolve()),'sourceSha256':hashlib.sha256(raw).hexdigest(),'historicalCapture':'2026-09-06 directory; not current platform schema','limitations':['Default version only','Wrapper existence does not prove permission or business semantics','No network requests or JavaScript execution','Parameter object and shared role context require call-site evidence'],'entries':result}

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.output.resolve().is_relative_to(Path('/Users/bjn00003/BDHub/01-BDSystem-V2')):p.error('legacy output forbidden')
 r=extract(a.input);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'wrapperPaths':len(r['entries'])}))
