"""Three same-account HTTP lanes sharing a strict aggregate request pacer."""
import queue,threading,time
from concurrent.futures import ThreadPoolExecutor

class SharedPacer:
 def __init__(self,qps=3):self.interval=1/qps;self.lock=threading.Lock();self.last=0;self.starts=[]
 def acquire(self):
  # Hold the lock through sleep so delayed threads cannot bunch their starts.
  with self.lock:
   time.sleep(max(0,self.last+self.interval-time.monotonic()));self.last=time.monotonic();self.starts.append(self.last)

def run_find_cohort(probe,child,identity,scratch,targets,client,report,save,classify,summarize,collect,allowed):
 lock=threading.RLock();stop=threading.Event();work=queue.Queue();clients=[client];pacer=SharedPacer(3)
 try:
  for _ in range(2):
   c=probe.PureHttpPartnerClient(client.config,identity,scratch)
   c.business_retries=client.business_retries;c.captcha_attempts=client.captcha_attempts
   child._configure_market_transport(c,identity);clients.append(c)
 except BaseException:
  for c in clients[1:]:c.session.close()
  raise
 for c in clients:c.pacer=pacer
 rows=[]
 for t in targets:
  row={'targetRef':t['ref'],'externalId':t['externalId'],'inputKind':'handle_discovery','requestedHandle':t['handle'],'requestedOecId':None,'profiles':[]}
  rows.append(row);work.put((t,row))
 report['targets']=rows;report['httpLanes']=3;report['qps']=3
 def persist():
  totals={}
  for c in clients:
   for k,v in collect(c).items():
    if type(v) is int:totals[k]=totals.get(k,0)+v
  report['counters']=totals;report['requestStartTimes']=list(getattr(pacer,'starts',[]));save()
 def lane(c):
  while not stop.is_set():
   try:t,row=work.get_nowait()
   except queue.Empty:return
   if not allowed(t['ref']):
    with lock:row['status']='not_requested';report.update(status='blocked',reason='cohort_target_not_startable');persist()
    continue
   entry={'targetRef':t['ref'],'stage':'find','profileTypes':None,'status':'inflight','attempts':[],'verificationAttempts':[]};start=time.monotonic()
   with lock:report['requests'].append(entry);persist()
   original=c._signed_post_once;solve=c._solve_captcha
   def signed(stage,body):
    response,payload=original(stage,body)
    with lock:entry['attempts'].append({k:v for k,v in classify(response.status_code,response.headers,payload).items() if k!='allowed'});persist()
    return response,payload
   def verified(data,attempt):
    v={'attempt':attempt,'status':'inflight'}
    with lock:entry['verificationAttempts'].append(v);persist()
    try:
     result=solve(data,attempt)
     with lock:v['status']='returned';persist()
     return result
    except BaseException:
     with lock:v['status']='error';persist()
     raise
   c._signed_post_once=signed;c._solve_captcha=verified
   try:
    response,payload=c.post('find',{'query':t['handle'],'pagination':{'size':12,'page':0},'query_type':1,'filter_params':{},'algorithm':1})
    decision=classify(response.status_code,response.headers,payload)
    with lock:
     entry.update({k:v for k,v in decision.items() if k!='allowed'},status='returned',durationMs=round((time.monotonic()-start)*1000,1))
     if not decision['allowed']:
      report.update(status='blocked',reason='verification_required' if decision['verificationRequired'] else 'remote_error');stop.set();persist();continue
     exact=probe.find_exact(payload,t['handle'])
     if not isinstance(exact,dict):row.update(status='unresolved',reason='no_exact_handle',currentHandleResolved=False)
     else:
      summary=summarize(exact);oec=child._oec(exact)
      if summary['identity']['market']!='it' or not oec.isascii() or not oec.isdigit() or child._handle(exact).lower()!=t['handle']:
       report.update(status='blocked',reason='find_market_mismatch');stop.set()
      else:row.update(status='identity_verified',oecId=oec,find=summary,currentHandleResolved=True,currentPlatformIdentityVerified=True,historicalCrossSourceIdentityProven=False,profileCollection='not_requested')
     persist()
   except Exception as e:
    with lock:
     entry.update(status='error',errorType=type(e).__name__,durationMs=round((time.monotonic()-start)*1000,1));report.update(status='blocked',reason='request_or_signer_error');stop.set();persist()
   finally:c._signed_post_once=original;c._solve_captcha=solve
 try:
  with ThreadPoolExecutor(max_workers=3) as executor:
   futures=[executor.submit(lane,c) for c in clients]
   for f in futures:f.result()
  with lock:persist();client.cohort_counters=dict(report['counters'])
 finally:
  for c in clients[1:]:c.session.close()
