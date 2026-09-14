#!/usr/bin/env python3
"""Exercise real ACC9 collector entrypoints beside ACC6 IM reads, without sends or source publication."""
import json,sqlite3,subprocess,sys,time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_live_runtime import _authenticated
from lib.italy_im_session import ItalyImReadSession

def main():
    folder=ROOT/'var'/('catalog-read-routing-'+str(int(time.time())));folder.mkdir(mode=0o700)
    summary={'folder':str(folder),'realSends':0,'platformWrites':0,'productionSnapshotWrites':0,'steps':[]}
    def collectors():
        jobs=[('global',[str(ROOT/'scripts/collect-global-opportunity.py'),'--run-id','acc9-entry-canary','--pages','1','--audit-store',str(folder/'global.sqlite')],folder/'global.report.json')]
        jobs +=[(source,[str(ROOT/'scripts/sync-cycle-catalog.py'),'--source',source,'--run',str(folder/(source+'.json')),'--max-requests','2','--audit-only'],folder/(source+'.json')) for source in ('selected','campaign')]
        for name,args,path in jobs:
            proc=subprocess.run([sys.executable,*args],cwd=ROOT,capture_output=True,text=True,timeout=140)
            if proc.returncode or not path.exists():raise ValueError('collector_entry_failed_'+name)
            record=json.loads(path.read_text());transport=record if name=='global' else record.get('transportReport',{})
            if record.get('error') or transport.get('scope',{}).get('account')!='acc9' or transport.get('identityFileUnchanged') is not True:raise ValueError('collector_actor_not_verified_'+name)
            summary['steps'].append({'source':name,'account':'acc9','guardAcquiredAt':transport['guardAcquiredAt'],'guardReleasedAt':transport['guardReleasedAt'],
                'requests':1 if name=='global' else record['requests'],'records':record['status']['products'] if name=='global' else len(record['offers']),'identityFileUnchanged':True,'report':str(path)})
    report={}
    try:
        with _authenticated(report,stopped=lambda:False) as (_,_,_,auth,maintenance,_):
            summary['imGuardAcquiredAt']=time.time()
            with closing(sqlite3.connect((ROOT/'var/it-conversations.sqlite').as_uri()+'?mode=ro',uri=True)) as c:
                cid,oec,kind=c.execute("SELECT cid,oec,kind FROM conversation WHERE scope='it:acc6' AND kind=2 ORDER BY cid LIMIT 1").fetchone()
            with ThreadPoolExecutor(max_workers=1) as pool,ItalyImReadSession(auth,report,maintenance_due=maintenance) as reads:
                work=pool.submit(collectors);conv=reads.conversation(cid,oec,conversation_type=kind);reads.history_summary(conv)
                summary['imReadDuringCollectors']=True
                work.result(timeout=420)
            summary['imGuardReleasedAt']=time.time()
        summary['imIdentityFileUnchanged']=report.get('identityFileUnchanged') is True
        for step in summary['steps']:
            step['overlapSeconds']=round(max(0,min(step['guardReleasedAt'],summary['imGuardReleasedAt'])-max(step['guardAcquiredAt'],summary['imGuardAcquiredAt'])),3)
        summary['passed']=len(summary['steps'])==3 and all(s['overlapSeconds']>0 for s in summary['steps']) and summary['imIdentityFileUnchanged']
    except Exception as e:summary.update(passed=False,error=str(e) if isinstance(e,ValueError) else getattr(e,'code',type(e).__name__))
    (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
