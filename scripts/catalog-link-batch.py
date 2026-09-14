#!/usr/bin/env python3
"""Advance the catalog link queue: read in bounded passes, then create only confirmed gaps.

Safe to stop and restart: every pass resumes from the durable ledger and never
repeats a verified creation.
"""
import json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import argparse
def run(args,label,timeout=3000):
    stamp=time.strftime('%Y%m%d-%H%M%S')
    report=ROOT/f'var/catalog-link-{label}-{stamp}.json'
    while report.exists():report=report.with_name(report.stem+'-b.json')
    cmd=[sys.executable,str(ROOT/'scripts/catalog-link-prepare.py'),*args,'--report',str(report)]
    started=time.time()
    child=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=timeout)
    out=(child.stdout or '').strip().splitlines()
    return {'label':label,'report':str(report.relative_to(ROOT)),'exitCode':child.returncode,'elapsedSeconds':round(time.time()-started,1),
            'result':json.loads(out[-1]) if out else None,'stderr':(child.stderr or '')[-400:] or None}
def main():
    p=argparse.ArgumentParser();p.add_argument('--pids');p.add_argument('--limit',type=int,default=15);p.add_argument('--passes',type=int,default=80);p.add_argument('--creates',type=int,default=0)
    p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    out=a.report.resolve()
    if not out.is_relative_to(ROOT/'var') or out.exists():p.error('new report under var required')
    steps=[run(['seed'],'seed')]
    for i in range(a.passes):
        r=run(['read','--limit',str(a.limit)]+(['--pids',a.pids] if a.pids else []),f'read-{i:02d}')
        steps.append(r)
        states=(r.get('result') or {}).get('summary',{}).get('states',{})
        if (r.get('result') or {}).get('summary',{}).get('pendingCount',1)==0:break
        if r['exitCode']!=0:break
    if a.creates:
        while True:
            r=run(['create','--max-creates',str(a.creates)],f'create-{len(steps):02d}')
            steps.append(r)
            created=(r.get('result') or {}).get('create',{}).get('created',[])
            if r['exitCode']!=0 or not created:break
    last=next((s.get('result') for s in reversed(steps) if s.get('result')),None)
    summary=(last or {}).get('summary')
    out.write_text(json.dumps({'steps':steps,'realSends':0,'finalSummary':summary},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'steps':len(steps),'finalSummary':summary},ensure_ascii=False))
if __name__=='__main__':main()
