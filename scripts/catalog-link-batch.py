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
def publish(path,payload):
    """Write the running step so the page can show how far the batch has got.

    Each pass takes minutes, so a progress bar fed only by the final report would sit still and
    read like a hung job. The file is small and replaced atomically; the launcher reads it back.
    """
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload,ensure_ascii=False)+'\n',encoding='utf-8')
    temporary.replace(path)
def main():
    p=argparse.ArgumentParser();p.add_argument('--pids');p.add_argument('--limit',type=int,default=15);p.add_argument('--passes',type=int,default=80);p.add_argument('--creates',type=int,default=0);p.add_argument('--seed',action='store_true')
    p.add_argument('--route',choices=['selected','campaign'],default='selected',
                   help='selected=全托（账号级卡）；campaign=非全托（按活动卡，播种来自已入池商品）')
    p.add_argument('--lanes',type=int,default=1,choices=[1,3,6,9]);p.add_argument('--qps',type=int,default=3,choices=[3,5,8,12])
    p.add_argument('--progress',type=Path,help='running-step file the launcher reads while the batch runs')
    p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    out=a.report.resolve()
    if not out.is_relative_to(ROOT/'var') or out.exists():p.error('new report under var required')
    progress=a.progress.resolve() if a.progress else None
    if progress is not None and not progress.is_relative_to(ROOT/'var'):p.error('progress must live under var')
    route=['--route',a.route]
    steps=[];created=0;startedAt=time.time()
    def note(phase,pass_no=None):
        if progress is None:return
        last=next((s.get('result') for s in reversed(steps) if s.get('result')),None) or {}
        summary=last.get('summary') or {}
        publish(progress,{'startedAt':startedAt,'updatedAt':time.time(),'phase':phase,'step':len(steps),
                          'pass':pass_no,'passes':a.passes,'created':created,
                          'total':int(summary.get('total') or 0),'states':summary.get('states') or {}})
    if a.seed:
        steps.append(run(['seed',*route,'--lanes',str(a.lanes),'--qps',str(a.qps)],'seed'));note('seed')
    if a.route=='campaign':
        # 非全托的判定读的是**本地缓存 + 池子事实**，一次领完就能把所有未结行复判完；
        # 而且缺链的行合法地停在 missing（要等建链），用 pendingCount 当收敛条件会白跑满 --passes。
        for i in range(a.passes):
            r=run(['read',*route,'--limit','600','--lanes',str(a.lanes),'--qps',str(a.qps)],f'read-{i:02d}')
            steps.append(r);note('read',i+1)
            summary=(r.get('result') or {}).get('summary') or {};states=summary.get('states') or {}
            if r['exitCode']!=0 or not states.get('pending',0)+states.get('reading',0):break
    else:
        for i in range(a.passes):
            r=run(['read',*route,'--limit',str(a.limit),'--lanes',str(a.lanes),'--qps',str(a.qps)]+(['--pids',a.pids] if a.pids else []),f'read-{i:02d}')
            steps.append(r);note('read',i+1)
            if (r.get('result') or {}).get('summary',{}).get('pendingCount',1)==0:break
            if r['exitCode']!=0:break
    if a.creates:
        while True:
            r=run(['create',*route,'--max-creates',str(a.creates),'--lanes',str(a.lanes),'--qps',str(a.qps)],f'create-{len(steps):02d}')
            steps.append(r)
            made=(r.get('result') or {}).get('created',0)
            created+=made if isinstance(made,int) else 0
            note('create')
            if r['exitCode']!=0 or not made:break
    last=next((s.get('result') for s in reversed(steps) if s.get('result')),None)
    summary=(last or {}).get('summary')
    if progress is not None:
        last=last or {};summary=summary or {}
        publish(progress,{'startedAt':startedAt,'updatedAt':time.time(),'phase':'done','step':len(steps),
                          'pass':None,'passes':a.passes,'created':created,
                          'total':int(summary.get('total') or 0),'states':summary.get('states') or {}})
    out.write_text(json.dumps({'steps':steps,'realSends':0,'finalSummary':summary},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'steps':len(steps),'finalSummary':summary},ensure_ascii=False))
if __name__=='__main__':main()
