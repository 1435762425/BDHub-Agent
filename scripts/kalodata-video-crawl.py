#!/usr/bin/env python3
"""Initialize, run or inspect the durable full-scope Kalodata video crawl."""
import argparse
from datetime import date,timedelta
import json
import sqlite3
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
LEGACY=ROOT.parent/'01-BDSystem-V2'
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.kalodata_video_scan import initialize,mark_stopped,scan_one,status  # noqa:E402
from lib.kalodata_video_transport import live_provider  # noqa:E402
from lib.leads_queue import eligible_products,linked_products  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402


def latest_generation():
 with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  row=store.db.execute('SELECT generation_id FROM kalodata_video_generation ORDER BY created_at DESC LIMIT 1').fetchone()
 return row[0] if row else None


def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('init','run','status'))
 parser.add_argument('--generation');args=parser.parse_args(argv)
 try:
  if args.action=='init':
   if args.generation:raise CycleError('video_scan_arguments_invalid')
   products=eligible_products(ROOT);linked=linked_products(ROOT)
   scope=[{'pid':pid,'units':int(products[pid].get('units') or 0)} for pid in set(products)&set(linked)]
   end=date.today()-timedelta(days=2);start=end-timedelta(days=29)
   result=initialize(ROOT,scope,str(start),str(end));result|={'platformWrites':0,'realSends':0}
   print(json.dumps(result,ensure_ascii=False));return 0
  generation=args.generation or latest_generation()
  if not generation:raise CycleError('video_scan_generation_missing')
  if args.action=='status':
   print(json.dumps(status(ROOT,generation),ensure_ascii=False));return 0
  while True:
   snapshot=status(ROOT,generation);current=snapshot.get('current')
   if not current:print(json.dumps(snapshot,ensure_ascii=False));return 0
   pid=current['pid']
   with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
    gen=store.db.execute('SELECT window_start,window_end FROM kalodata_video_generation WHERE generation_id=?',
                         (generation,)).fetchone()
   with live_provider(ROOT,LEGACY,pid,gen['window_start'],gen['window_end']) as provider:
    scan_one(ROOT,generation,provider.request)
 except (CycleError,BlockingIOError,OSError,ValueError,sqlite3.Error) as error:
  # A storage error must still end in a report: without one the scheduler only sees a report-less child.
  code=str(error) if isinstance(error,(CycleError,ValueError)) else \
       'kalodata_video_storage_failed' if isinstance(error,sqlite3.Error) else 'kalodata_video_unavailable'
  if 'generation' in locals() and generation:
   try:mark_stopped(ROOT,generation,code)
   except (CycleError,OSError,ValueError,sqlite3.Error):pass
  result={'error':code,'platformWrites':0,'realSends':0}
  if 'generation' in locals() and generation:
   try:result['status']=status(ROOT,generation)
   except (CycleError,OSError,ValueError,sqlite3.Error):pass
  print(json.dumps(result,ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
