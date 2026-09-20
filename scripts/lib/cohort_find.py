"""Three same-account HTTP lanes sharing a strict aggregate request pacer."""
import queue,threading,time
from concurrent.futures import ThreadPoolExecutor

class SharedPacer:
 def __init__(self,qps=3):self.interval=1/qps;self.lock=threading.Lock();self.last=0;self.starts=[]
 def acquire(self):
  # Hold the lock through sleep so delayed threads cannot bunch their starts.
  with self.lock:
   time.sleep(max(0,self.last+self.interval-time.monotonic()));self.last=time.monotonic();self.starts.append(self.last)

def run_find_cohort(probe,child,identity,scratch,targets,client,report,save,classify,summarize,collect,allowed,lanes=3,qps=3):
 if type(qps) is not int or qps not in (3,5,8,12):raise ValueError('invalid_cohort_qps')
 if type(lanes) is not int or lanes not in (3,6,9):raise ValueError('invalid_cohort_lanes')
 lock=threading.RLock();verification_lock=threading.Lock();shared_verification={'result':None,'client':None}
 stop=threading.Event();work=queue.Queue();clients=[client];pacer=SharedPacer(qps)
 try:
  for _ in range(lanes-1):
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
 report['targets']=rows;report['httpLanes']=lanes;report['qps']=qps
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
   original=c._signed_post_once;solve=c._solve_captcha;http_post=c.session.post
   def network_post(*args,**kwargs):
    before=time.monotonic()
    try:return http_post(*args,**kwargs)
    finally:
     with lock:entry['networkMs']=entry.get('networkMs',0)+round((time.monotonic()-before)*1000,1)
   c.session.post=network_post
   def signed(stage,body):
    response,payload=original(stage,body)
    with lock:entry['attempts'].append({k:v for k,v in classify(response.status_code,response.headers,payload).items() if k!='allowed'});persist()
    return response,payload
   def verified(data,attempt):
    v={'attempt':attempt,'status':'inflight'}
    with lock:entry['verificationAttempts'].append(v);persist()
    try:
     with verification_lock:
      previous=shared_verification['result'] if attempt==1 else None
      source=shared_verification['client'] if previous is not None else None
      if previous is None:
       result=solve(data,attempt);shared_verification.update(result=result,client=c);source=c
      else:result=previous
      if source is not None and source is not c and hasattr(source,'session') and hasattr(c,'session') and \
         hasattr(source.session,'cookies') and hasattr(c.session,'cookies'):
       for name,value in source.session.cookies.get_dict().items():c.session.cookies.set(name,value)
       if hasattr(source,'fp'):c.fp=source.fp
     with lock:v['status']='shared' if previous is not None else 'returned';persist()
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
     entry.update(status='error',errorType=type(e).__name__,errorCategory=('tls_certificate' if 'certificate verify failed' in str(e).lower() else 'tls_eof' if 'eof' in str(e).lower() else 'timeout' if 'timeout' in str(e).lower() or 'timed out' in str(e).lower() else 'transport_or_signer'),durationMs=round((time.monotonic()-start)*1000,1));report.update(status='blocked',reason='request_or_signer_error');stop.set();persist()
   finally:
    c._signed_post_once=original;c._solve_captcha=solve;c.session.post=http_post
    with lock:entry['clientAndPacingMs']=round(max(0,entry.get('durationMs',0)-entry.get('networkMs',0)),1)
 try:
  with ThreadPoolExecutor(max_workers=lanes) as executor:
   futures=[executor.submit(lane,c) for c in clients]
   for f in futures:f.result()
  with lock:persist();client.cohort_counters=dict(report['counters'])
 finally:
  for c in clients[1:]:c.session.close()
