#!/usr/bin/env python3
"""Collect or inspect exact-PID Kalodata video evidence; never changes the send pool."""
import argparse
from datetime import date,timedelta
import fcntl
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
LEGACY=ROOT.parent/'01-BDSystem-V2'
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.kalodata_video_evidence import (VIDEO_DETAIL_PATH,VIDEO_LIST_PATH,collect,persist,status)  # noqa:E402
from lib.kalodata_video_transport import live_provider  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402


def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('status','probe'))
 parser.add_argument('--pid');parser.add_argument('--days',type=int,default=30)
 parser.add_argument('--min-views',type=int,default=1000)
 args=parser.parse_args(argv)
 try:
  if args.pid is not None and not re.fullmatch(r'[0-9]{19}',args.pid):raise CycleError('kalodata_video_scope_invalid')
  if args.action=='status':
   with CycleStore(ROOT/'var/second-cycle.sqlite') as store:result=status(ROOT,store,args.pid)
   print(json.dumps(result,ensure_ascii=False));return 0
  if not args.pid or not 7<=args.days<=90 or not 1<=args.min_views<=1_000_000_000:
   raise CycleError('kalodata_video_scope_invalid')
  end=date.today()-timedelta(days=2);start=end-timedelta(days=args.days-1)
  with (ROOT/'var/kalodata-video-evidence.lock').open('a') as own:
   fcntl.flock(own,fcntl.LOCK_EX|fcntl.LOCK_NB)
   with live_provider(ROOT,LEGACY,args.pid,str(start),str(end)) as provider:
    report=collect(args.pid,str(start),str(end),provider.request,min_views=args.min_views)
   report['cookieFileUnchanged']=provider.unchanged
   with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
    stored=persist(store,report);snapshot=status(ROOT,store,args.pid)
  result={'schema':report['schema'],'runId':report['runId'],'pid':args.pid,'state':report['state'],
          'rowsReceived':report['rowsReceived'],'qualifyingVideos':report['qualifyingVideos'],
          'selectedVideos':report['selectedVideos'],'resolvedVideos':report['resolvedVideos'],
          'coverage':report['coverage'],'pagesRead':report['pagesRead'],'networkRequests':report['networkRequests'],
          'errors':report['errors'],'stored':stored,'candidates':snapshot['candidates'],
          'cookieFileUnchanged':report['cookieFileUnchanged'],'platformWrites':0,'realSends':0,
          'sendPoolChanged':False}
  print(json.dumps(result,ensure_ascii=False));return 0
 except (CycleError,BlockingIOError,OSError,ValueError) as error:
  code=str(error) if isinstance(error,(CycleError,ValueError)) else 'kalodata_video_unavailable'
  print(json.dumps({'error':code},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
