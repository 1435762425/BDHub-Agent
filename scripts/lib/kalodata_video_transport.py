"""Shared read-only Kalodata video transport with the repository browser lock."""
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
from pathlib import Path
import re
import time

from lib.cycle_kalodata import quota_exhausted
from lib.kalodata_video_evidence import VIDEO_DETAIL_PATH,VIDEO_LIST_PATH
from lib.legacy_runtime import configure_vendored_bdhub
from lib.second_cycle import CycleError


class VideoProvider:
 def __init__(self,root,legacy,pid,start,end):
  self.root=Path(root);self.legacy=Path(legacy);self.pid=pid;self.start=start;self.end=end
 def __enter__(self):
  configure_vendored_bdhub(root=self.root,legacy_root=self.legacy)
  from bdhub import config
  from curl_cffi import requests
  path=config.load().kalodata.project_dir/'product_top50/collect.py'
  spec=importlib.util.spec_from_file_location('video_evidence_kalodata_core',path)
  core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
  self.cookie_path=core.COOKIE_PATH;self.before=hashlib.sha256(self.cookie_path.read_bytes()).hexdigest()
  self.headers=core.build_headers(core.read_cookie(),self.pid,'IT','EUR');self.proxy=core.load_proxy_url()
  self.session=requests.Session(impersonate='chrome');self.last=0.;self.requests=0
  return self
 def __exit__(self,*_):
  self.session.close();self.unchanged=hashlib.sha256(self.cookie_path.read_bytes()).hexdigest()==self.before
 def request(self,path,payload):
  if path not in (VIDEO_LIST_PATH,VIDEO_DETAIL_PATH) or payload.get('startDate')!=self.start or payload.get('endDate')!=self.end:
   raise CycleError('kalodata_video_endpoint_forbidden')
  if path==VIDEO_LIST_PATH:
   sort=payload.get('sort')
   if payload.get('id')!=self.pid or type(payload.get('pageNo')) is not int or payload['pageNo']<1 or \
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
def live_provider(root,legacy,pid,start,end):
 lock_path=Path(legacy)/'data/research/kalodata/.browser.lock'
 with lock_path.open('rb') as lock:
  fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
  with VideoProvider(root,legacy,pid,start,end) as provider:yield provider
