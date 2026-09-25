"""Private same-user IPC for one profile owner; credentials are never persisted or logged.

Borrowing authentication does not authorize a send. Existing dispatch permits, request ledgers,
market write gates, and original-account reconciliation remain authoritative.
"""
import json,os,socket,socketserver,stat,threading,time,uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from lib.italy_im_auth import ItalyImAuthContext

MAX_FRAME=131072
class OwnerUnavailable(RuntimeError):pass

def enabled(root,market):
    p=Path(root)/'config/market-accounts.json'
    if not p.exists():return False
    value=json.loads(p.read_text());mode=value.get('markets',{}).get(market,{}).get('imSessionMode','http_polling')
    if mode not in ('http_polling','sdk_http'):raise ValueError('im_session_mode_invalid')
    return mode=='sdk_http'

def identity_account(root,market,*,pair=None):
    from lib.market_accounts import load_config
    if pair is None and market=='it' and not (Path(root)/'config/market-accounts.json').exists():return 'acc6'
    pair=pair if pair is not None else load_config(root)['markets'][market]
    role=pair.get('identityAccountRole','communications')
    if role not in ('communications','supply'):raise ValueError('identity_account_role_invalid')
    return pair['roles'][role]

def maintenance_pending(root,account):
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect((Path(root)/'var/second-cycle.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        return bool(db.execute("SELECT 1 FROM account_maintenance_intent WHERE account=? AND state IN ('queued','draining','running')",(account,)).fetchone())

def paths(root,market):
    if market not in ('it','br','my','uk'):raise ValueError('im_session_market_invalid')
    base=Path(root)/'var'
    return base/f'im-session-{market}.sock',base/f'im-session-{market}.json'

def _put(file,value):
    raw=json.dumps(value,separators=(',',':')).encode()+b'\n'
    if len(raw)>MAX_FRAME:raise ValueError('im_session_frame_large')
    file.write(raw);file.flush()

def _get(file):
    line=file.readline(MAX_FRAME+1)
    if not line or len(line)>MAX_FRAME or not line.endswith(b'\n'):raise ValueError('im_session_frame_invalid')
    value=json.loads(line)
    if not isinstance(value,dict):raise ValueError('im_session_frame_invalid')
    return value

class OwnerState:
    def __init__(self,market,account,auth,valid):
        self.market,self.account,self.auth,self.valid=market,account,auth,valid
        self.epoch=uuid.uuid4().hex;self.accepting=False;self.alive=True;self.clients=0
        self.condition=threading.Condition()
    def borrow(self,market,account):
        with self.condition:
            if not self.alive or not self.accepting or market!=self.market or account!=self.account or not self.valid():raise ValueError('im_session_unavailable')
            self.clients+=1
            return {'ok':True,'epoch':self.epoch,'auth':asdict(self.auth)}
    def check(self,epoch):
        with self.condition:
            return self.alive and epoch==self.epoch and bool(self.valid())
    def release(self):
        with self.condition:self.clients-=1;self.condition.notify_all()
    def drain(self):
        with self.condition:
            self.accepting=False
            while self.clients:self.condition.wait(1)
            self.alive=False

class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        acquired=False
        self.request.settimeout(180)
        try:
            first=_get(self.rfile)
            if set(first)!={'op','market','account'} or first['op']!='borrow':raise ValueError('im_session_request_invalid')
            value=self.server.owner.borrow(first['market'],first['account']);acquired=True
            _put(self.wfile,value)
            while True:
                req=_get(self.rfile)
                if req.get('op')=='release':break
                if set(req)!={'op','epoch'} or req['op']!='check':raise ValueError('im_session_request_invalid')
                if not self.server.owner.check(req['epoch']):raise ValueError('im_session_changed')
                _put(self.wfile,{'ok':True})
        except Exception:
            try:_put(self.wfile,{'ok':False,'error':'im_session_unavailable'})
            except Exception:pass
        finally:
            if acquired:self.server.owner.release()

class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True
    def handle_error(self,*_):pass  # Never log credential frames or untrusted exception payloads.

@contextmanager
def serve(root,market,state):
    path,_=paths(root,market);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() or path.is_symlink():path.unlink()  # Caller owns the market singleton lock.
    old=os.umask(0o177)
    try:server=_Server(str(path),_Handler)
    finally:os.umask(old)
    path.chmod(0o600);server.owner=state
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield state
    finally:
        state.drain();server.shutdown();server.server_close();thread.join();path.unlink(missing_ok=True)

@contextmanager
def borrow(root,market,account,*,stopped=lambda:False):
    path,_=paths(root,market);sock=None;file=None
    def close():
        if file:
            try:_put(file,{'op':'release'})
            except Exception:pass
            try:file.close()
            except OSError:pass
        if sock:sock.close()
    try:
        info=path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise ValueError('im_session_socket_invalid')
        if stopped():raise ValueError('im_session_stopped')
        sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);sock.settimeout(5);sock.connect(str(path));file=sock.makefile('rwb')
        _put(file,{'op':'borrow','market':market,'account':account});value=_get(file)
        if not value.get('ok'):raise ValueError('im_session_unavailable')
        auth=ItalyImAuthContext(**value['auth'])
        if auth.account_name!=account or auth.native_context.get('market')!=market:raise ValueError('im_session_identity_mismatch')
    except Exception:
        close();raise OwnerUnavailable('im_session_unavailable') from None
    def check():
        try:
            if stopped():raise ValueError('im_session_stopped')
            _put(file,{'op':'check','epoch':value['epoch']})
            if not _get(file).get('ok'):raise ValueError('im_session_changed')
        except Exception:raise OwnerUnavailable('im_session_changed') from None
        return False
    try:
        check();yield auth,check
    finally:close()

def state(root,market):
    from lib.process_liveness import pid_alive
    _,path=paths(root,market)
    try:value=json.loads(path.read_text())
    except (OSError,ValueError):value={}
    return value|{'running':pid_alive(value.get('pid'))}

def launch(root,market):
    import subprocess
    root=Path(root);current=state(root,market)
    if current['running']:return current
    log=root/f'var/im-session-{market}.log'
    with log.open('a') as handle:
        child=subprocess.Popen([str(root/'.venv/bin/python'),str(root/'scripts/im-session-worker.py'),'--market',market],cwd=root,stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    result={'pid':child.pid,'startedAt':time.time(),'state':'starting'}
    publish(root,market,result)
    if market=='it':
        # Existing jobs page and stop control continue to own the inbox entry.
        (root/'var/job-inbox.json').write_text(json.dumps({'name':'inbox','label':'收信','pid':child.pid,'startedAt':result['startedAt'],'config':{'limit':12,'interval':30},'log':str(log),'platformWrites':False})+'\n')
    return result|{'running':True}

def publish(root,market,value):
    _,path=paths(root,market);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False)+'\n');tmp.replace(path)
