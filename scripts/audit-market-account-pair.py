#!/usr/bin/env python3
"""Record a secret-free project-identity and independent-lease proof for one market pair."""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.account_identity import current_generation  # noqa:E402
from lib.legacy_runtime import configure_vendored_bdhub  # noqa:E402
from lib.market_accounts import load_config  # noqa:E402
from lib.second_cycle import CycleStore  # noqa:E402


def fingerprint(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--market',required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
 output=args.output.resolve()
 if not output.is_relative_to((ROOT/'var').resolve()):parser.error('output must be under var')
 pair=load_config(ROOT)['markets'].get(args.market)
 if not pair:parser.error('market assignment missing')
 configure_vendored_bdhub(root=ROOT,legacy_root=ROOT.parent/'01-BDSystem-V2')
 from bdhub import config
 from bdhub.enrich.profile_lease import ProfileLease
 accounts={row.name:row for row in config.load_accounts(config.load())}
 rows=[]
 with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=True) as store:
  generations={name:current_generation(store,args.market,name) for name in pair['accounts']}
 first,second=(accounts[name] for name in pair['accounts']);before={row.name:fingerprint(row.headers_json) for row in (first,second)}
 started=time.time();lease1=ProfileLease(first.profile_dir,account=first.name,market=args.market,operation='market-pair-audit')
 lease2=ProfileLease(second.profile_dir,account=second.name,market=args.market,operation='market-pair-audit')
 with lease1:
  with lease2:
   overlap_started=time.time();time.sleep(0.25);overlap=time.time()-overlap_started
 for account in (first,second):
  generation=generations[account.name];capabilities={key:value['state'] for key,value in (generation or {}).get('capabilities',{}).items()}
  rows.append({'account':account.name,'market':args.market,'state':'passed_readonly' if generation else 'missing_generation',
   'startedAt':started,'identityFileUnchanged':fingerprint(account.headers_json)==before[account.name],
   'capabilities':capabilities,'tokenExpiryFields':[]})
 institutions={generations[name]['institutionFingerprint'] for name in pair['accounts'] if generations[name]}
 passed=len(rows)==2 and all(row['state']=='passed_readonly' and row['identityFileUnchanged'] for row in rows) and len(institutions)==1
 report={'schema':'bdhub.market-account-pair.v1','market':args.market,'passed':passed,'sameInstitution':len(institutions)==1,
  'sameMarket':True,'sameSender':False,'independentGuardsOverlapSeconds':round(overlap,3),'sharedCardIds':[],
  'accounts':rows,'checkedAt':time.time(),'platformWrites':0,'realSends':0,'credentialWrites':0}
 output.parent.mkdir(parents=True,exist_ok=True);temporary=output.with_suffix(output.suffix+'.tmp')
 temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.chmod(temporary,0o600);temporary.replace(output)
 print(json.dumps({'market':args.market,'passed':passed,'accounts':[row['account'] for row in rows],
  'sameInstitution':report['sameInstitution'],'independentGuardsOverlapSeconds':report['independentGuardsOverlapSeconds'],
  'platformWrites':0,'realSends':0},ensure_ascii=False));return 0 if passed else 2


if __name__=='__main__':raise SystemExit(main())
