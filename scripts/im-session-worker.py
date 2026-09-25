#!/usr/bin/env python3
"""One communications profile owner: SDK receive callbacks plus leased existing HTTP clients."""
import argparse,fcntl,hashlib,json,os,signal,sys,time,threading
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.im_session_owner import OwnerState,serve,publish,enabled,maintenance_pending
from lib.market_im_runtime import authenticated
from lib.sdk_inbox import Receiver,arm_script,ready_script,DRAIN,ACK
from lib.legacy_runtime import project_identity_paths,project_account_enabled
STOP=False

def stop(*_):
    global STOP;STOP=True

def stopped(market):
    return STOP or not enabled(ROOT,market) or (market=='it' and (ROOT/'var/job-inbox.stop').exists())

def cycle(market):
    from lib.legacy_runtime import configure_vendored_bdhub
    configure_vendored_bdhub(root=ROOT,legacy_root=ROOT.parent/'01-BDSystem-V2')
    from bdhub.imbase.transport import _FIND_API
    from bdhub.hub.markets import im_page
    from playwright.sync_api import sync_playwright
    from lib.market_accounts import load_config
    account_name=load_config(ROOT)['markets'][market]['roles']['communications']
    if maintenance_pending(ROOT,account_name):raise ValueError('account_maintenance_pending')
    report={};closing=threading.Event();receiver=None
    with authenticated(ROOT,market,report,read_only=True,owner=True,stopped=lambda:stopped(market)) as runtime:
        account=runtime['account'];paths=project_identity_paths(ROOT,account.name)
        if not paths or Path(account.profile_dir).resolve()!=paths['profileDir'].resolve():raise ValueError('im_owner_project_profile_required')
        original=hashlib.sha256(Path(account.headers_json).read_bytes()).hexdigest();generation=paths['candidateId'];started=time.monotonic()
        def current():
            if not project_account_enabled(ROOT,account.name):return False
            if maintenance_pending(ROOT,account.name):return False
            if hashlib.sha256(Path(account.headers_json).read_bytes()).hexdigest()!=original:return False
            if (project_identity_paths(ROOT,account.name) or {}).get('candidateId')!=generation:return False
            return not runtime['session'].maintenance_due()
        owner=OwnerState(market,account.name,runtime['auth'],current)
        # Serve in another thread so HTTP dispatch does not wait for the browser's receive tick.
        with serve(ROOT,market,owner):
          with sync_playwright() as playwright:
            context=playwright.chromium.launch_persistent_context(str(account.profile_dir),headless=True,service_workers='block')
            try:
                # HTTP authentication has already proved this locked account. Keep HTTP
                # sends and backfill available while the browser hydrates its conversation list.
                owner.accepting=True
                def receive_stopped():return closing.is_set() or stopped(market)
                receiver=Receiver(ROOT,market,runtime['auth'],runtime['session'].maintenance_due,receive_stopped);receiver.start()
                def route(r):
                    path=urlsplit(r.request.url).path.rstrip('/')
                    if path.endswith('/message/send') or '/conversation/create' in path:r.abort()
                    else:r.continue_()
                context.route('**/*',route)
                page=context.pages[0] if context.pages else context.new_page();matches=[]
                def observe(response):
                    if urlsplit(response.url).path.endswith('/im/id/get'):
                        try:
                            data=response.json();data=data.get('data') if isinstance(data.get('data'),dict) else data
                            matches.append(str(data.get('im_id'))==runtime['auth'].im_id)
                        except Exception:pass
                page.on('response',observe);page.goto(im_page(market),wait_until='domcontentloaded',timeout=60000)
                if 'login' in page.url.lower():raise ValueError('sdk_login_required')
                for _ in range(240):
                    if stopped(market):return
                    if receiver.done.is_set():raise ValueError('sdk_http_reader_stopped')
                    if not current():raise ValueError('im_owner_identity_changed')
                    if page.evaluate(ready_script(_FIND_API)):break
                    page.wait_for_timeout(500)
                else:raise ValueError('sdk_not_ready')
                if not matches or not all(matches):raise ValueError('sdk_im_identity_unverified')
                armed=page.evaluate(arm_script(_FIND_API))
                if armed.get('armed')!=2:raise ValueError('sdk_callbacks_unavailable')
                print(json.dumps({'event':'session_ready','market':market,'epoch':owner.epoch,'at':time.time()}),flush=True)
                next_status=0;unready_since=None
                while not stopped(market) and time.monotonic()-started<600 and current():
                    if receiver.done.is_set():raise ValueError('sdk_http_reader_stopped')
                    ready=bool(page.evaluate(ready_script(_FIND_API)))
                    armed=page.evaluate(arm_script(_FIND_API)) if ready else {'armed':0}
                    if not ready or armed.get('armed')!=2:
                        if unready_since is None:unready_since=time.monotonic()
                        if time.monotonic()-unready_since>30:
                            diagnostic=page.evaluate('() => {'+_FIND_API+'return {api:!!api,status:api&&api.sdkStatus,loading:api&&api.isSDKLoading,sdkKeys:api&&api.sdkInstance?Object.keys(api.sdkInstance):[]};}')
                            print(json.dumps({'event':'sdk_state','market':market,'diagnostic':diagnostic,'at':time.time()}),flush=True)
                            if market=='my':
                                try:page.screenshot(path=str(ROOT/'var/im-session-my-diagnostic.png'))
                                except Exception:pass
                            raise ValueError('sdk_disconnected')
                        page.wait_for_timeout(250);continue
                    unready_since=None;data=page.evaluate(DRAIN)
                    if data.get('overflow'):raise ValueError('sdk_receive_buffer_overflow')
                    if data['events']:
                        receiver.signal(data['events'])  # Durable wakeups commit before clearing browser buffer.
                        page.evaluate(ACK,len(data['events']))
                    if time.time()>=next_status:
                        publish(ROOT,market,{'pid':os.getpid(),'state':'ready','market':market,'account':account.name,'generation':generation,'epoch':owner.epoch,'checkedAt':time.time(),'borrowedClients':owner.clients,'sdkReady':True,'receiver':dict(receiver.state)});next_status=time.time()+5
                    page.wait_for_timeout(250)
            finally:
                # Stop new clients; retain the profile until submitted HTTP calls finish.
                print(json.dumps({'event':'session_draining','market':market,'epoch':owner.epoch,'ageSeconds':round(time.monotonic()-started,1),'errorType':type(sys.exc_info()[1]).__name__ if sys.exc_info()[1] else None,'at':time.time()}),flush=True)
                owner.accepting=False;closing.set()
                if receiver:receiver.close()
                owner.drain()
                context.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('--market',required=True,choices=('it','br','my','uk'));a=p.parse_args();signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    with (ROOT/f'var/im-session-{a.market}.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        while not stopped(a.market):
            publish(ROOT,a.market,{'pid':os.getpid(),'state':'starting','market':a.market,'checkedAt':time.time()})
            try:cycle(a.market)
            except Exception as error:
                code=getattr(error,'code',None) or (str(error) if isinstance(error,ValueError) else type(error).__name__)
                print(json.dumps({'event':'session_error','market':a.market,'error':code,'at':time.time()}),flush=True)
                publish(ROOT,a.market,{'pid':os.getpid(),'state':'waiting_account' if code=='ProfileBusyError' else 'attention','error':code,'checkedAt':time.time(),'market':a.market})
                if getattr(error,'platform_code',None)==16201010 or code=='sdk_login_required':
                    from lib.second_cycle import CycleStore
                    from lib.login_recovery import request_refresh
                    with CycleStore(ROOT/'var/second-cycle.sqlite') as store:request_refresh(store,ROOT,a.market)
            for _ in range(3):
                if stopped(a.market):break
                time.sleep(1)
        publish(ROOT,a.market,{'pid':os.getpid(),'state':'stopped','checkedAt':time.time(),'market':a.market})
    return 0

if __name__=='__main__':raise SystemExit(main())
