#!/usr/bin/env python3
"""Collect or inspect exact-PID Kalodata video evidence; never changes the send pool."""
import argparse
from contextlib import contextmanager
from datetime import date,timedelta
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
LEGACY=ROOT.parent/'01-BDSystem-V2'
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.cycle_kalodata import quota_exhausted  # noqa:E402
from lib.kalodata_video_evidence import (VIDEO_DETAIL_PATH,VIDEO_LIST_PATH,collect,persist,status)  # noqa:E402
from lib.legacy_runtime import configure_vendored_bdhub  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402


class VideoProvider:
 def __init__(self,pid,start,end):self.pid=pid;self.start=start;self.end=end
 def __enter__(self):
  configure_vendored_bdhub(root=ROOT,legacy_root=LEGACY)
  from bdhub import config
  from curl_cffi import requests
  path=config.load().kalodata.project_dir/'product_top50/collect.py'
  spec=importlib.util.spec_from_file_location('video_evidence_kalodata_core',path)
  core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
  self.cookie_path=core.COOKIE_PATH;self.before=hashlib.sha256(self.cookie_path.read_bytes()).hexdigest()
  self.headers=core.build_headers(core.read_cookie(),self.pid,'IT','EUR');self.proxy=core.load_proxy_url()
  self.session=requests.Session(impersonate='chrome');self.last=0.;self.requests=0
  return self
 def __exit__(self,*_):self.session.close();self.unchanged=hashlib.sha256(self.cookie_path.read_bytes()).hexdigest()==self.before
 def request(self,path,payload):
  if path not in (VIDEO_LIST_PATH,VIDEO_DETAIL_PATH) or payload.get('startDate')!=self.start or payload.get('endDate')!=self.end:
   raise CycleError('kalodata_video_endpoint_forbidden')
  if path==VIDEO_LIST_PATH:
   sort=payload.get('sort')
   if payload.get('id')!=self.pid or type(payload.get('pageNo')) is not int or not 1<=payload['pageNo']<=20 or \
      payload.get('pageSize')!=50 or not isinstance(sort,list) or len(sort)!=1 or \
      sort[0].get('field') not in ('views','create_time') or sort[0].get('type')!='DESC':
    raise CycleError('kalodata_video_scope_invalid')
  elif not re.fullmatch(r'[0-9]{8,32}',str(payload.get('id') or '')):
   raise CycleError('kalodata_video_scope_invalid')
  time.sleep(max(0,1-(time.monotonic()-self.last)));self.last=time.monotonic();self.requests+=1
  response=self.session.post('https://www.kalodata.com'+path,headers=self.headers,json=payload,timeout=25,
      allow_redirects=False,proxies={'http':self.proxy,'https':self.proxy} if self.proxy else None)
  try:body=response.json()
  except ValueError:body={}
  if quota_exhausted(body):raise CycleError('kalodata_daily_quota_exhausted')
  if response.status_code in (401,403):raise CycleError('kalodata_auth_required')
  if response.status_code!=200 or not isinstance(body,dict) or body.get('success') is not True:
   raise CycleError('kalodata_video_read_failed')
  return body


@contextmanager
def live_provider(pid,start,end):
 with (LEGACY/'data/research/kalodata/.browser.lock').open('rb') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  with VideoProvider(pid,start,end) as provider:yield provider


def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('status','probe'))
 parser.add_argument('--pid');parser.add_argument('--days',type=int,default=30)
 parser.add_argument('--min-views',type=int,default=1000);parser.add_argument('--max-pages',type=int,default=20)
 args=parser.parse_args(argv)
 try:
  if args.pid is not None and not re.fullmatch(r'[0-9]{19}',args.pid):raise CycleError('kalodata_video_scope_invalid')
  if args.action=='status':
   with CycleStore(ROOT/'var/second-cycle.sqlite') as store:result=status(ROOT,store,args.pid)
   print(json.dumps(result,ensure_ascii=False));return 0
  if not args.pid or not 7<=args.days<=90 or not 1<=args.min_views<=1_000_000_000 or not 1<=args.max_pages<=20:
   raise CycleError('kalodata_video_scope_invalid')
  end=date.today()-timedelta(days=2);start=end-timedelta(days=args.days-1)
  with (ROOT/'var/kalodata-video-evidence.lock').open('a') as own:
   fcntl.flock(own,fcntl.LOCK_EX|fcntl.LOCK_NB)
   with live_provider(args.pid,str(start),str(end)) as provider:
    report=collect(args.pid,str(start),str(end),provider.request,min_views=args.min_views,max_pages=args.max_pages)
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
