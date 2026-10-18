"""Use the existing extension login without overwriting its newly issued identity."""
import json
import os
from pathlib import Path
import signal
import time
from urllib.parse import quote,urlsplit


def proxy_url(value):
    if isinstance(value,dict):
        host=str(value.get('host') or '').strip();port=str(value.get('port') or '').strip()
        if not host or not port.isdigit() or not 0<int(port)<65536:return None
        username=str(value.get('username') or '');password=str(value.get('password') or '')
        auth=f'{quote(username,safe="")}:{quote(password,safe="")}@' if username or password else ''
        return f'http://{auth}{host}:{port}'
    if not isinstance(value,str):return None
    value=value.strip()
    if '|' in value:
        parts=value.split('|')
        return proxy_url(dict(zip(('host','port','username','password'),parts))) if len(parts)==4 else None
    try:
        parsed=urlsplit(value)
        if parsed.scheme in ('http','https','socks5') and parsed.hostname and parsed.port:return value
    except ValueError:pass
    return None


def export_proxy_fallback(launcher,context,extension_id):
    """An empty currentProxy object must not hide the extension's saved proxy."""
    try:
        workers=list(context.service_workers)
    except TypeError:return False
    for worker in workers:
        if not worker.url.startswith(f'chrome-extension://{extension_id}/'):continue
        values=worker.evaluate("async () => ({runtime:typeof state!=='undefined'?state.currentProxy:null, saved:await chrome.storage.local.get(['proxy'])})")
        if not isinstance(values,dict):continue
        saved=values.get('saved') or {}
        url=proxy_url(values.get('runtime')) or proxy_url(saved.get('proxy') if isinstance(saved,dict) else None)
        if not url:continue
        target=Path(launcher.EXTENSION_RUNTIME_PATH)
        temporary=target.with_name(target.name+f'.{os.getpid()}.tmp')
        temporary.write_text(json.dumps({'proxy_url':url,'updated_at':time.time(),'source':'kalodata_extension'},indent=2)+'\n')
        temporary.chmod(0o600);temporary.replace(target);target.chmod(0o600)
        return True
    return False


def initialize_session(launcher,context,mode,code='',*,wait_seconds=0):
    extension_id=launcher.wait_for_extension_id(context)
    if not extension_id:raise ValueError('kalodata_extension_missing')
    if mode=='activate':
        if not code:raise ValueError('kalodata_activation_code_missing')
        launcher.activate_and_login_extension(context,extension_id,code)
    elif mode=='refresh':
        if not launcher.refresh_extension_login(context,extension_id):
            if not code:raise ValueError('kalodata_saved_activation_missing')
            launcher.activate_and_login_extension(context,extension_id,code)
    else:raise ValueError('kalodata_login_mode_invalid')
    # The extension issued cookies and the matching egress together. Importing an old
    # latest_cookie.txt here would undo that login and mix identities from different routes.
    if not launcher.export_extension_runtime(context,extension_id):
        deadline=time.monotonic()+wait_seconds
        while not export_proxy_fallback(launcher,context,extension_id):
            if time.monotonic()>=deadline:break  # The supplier protocol permits direct sessions.
            time.sleep(.5)
    launcher.export_current_cookie(context)
    return extension_id


def verified_probe(result):
    return isinstance(result,dict) and result.get('identityOk') is True and result.get('verdict') in ('ready','quota_exhausted')


def checkpoint(root,state,**details):
    from lib.kalodata_identity import login_state_path
    target=login_state_path(root);target.parent.mkdir(parents=True,exist_ok=True)
    value={**state,**details,'pid':os.getpid(),'checkedAt':time.time()}
    tmp=target.with_name(target.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(target)


def run_login(root,mode):
    from lib.kalodata_identity import export_helpers,load,probe,cookie_state
    launcher=export_helpers();code=os.environ.get('KALODATA_ACTIVATION_CODE') or load(root)['activationCode']
    state={'mode':mode,'startedAt':time.time(),'log':str(Path(root)/'var/kalodata-login.log')}
    stopped=[False]
    def stop(*_):stopped[0]=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    checkpoint(root,state,stage='extension_login')
    try:
        with launcher.sync_playwright() as playwright:
            context=launcher.launch_context(playwright)
            identity_ready=False
            try:
                initialize_session(launcher,context,mode,code)
                result=probe(root)
                identity_ready=verified_probe(result)
                cookie_at=cookie_state()['updatedAt'];last_probe=time.monotonic()
                print(json.dumps({'stage':'production_probe','verdict':result['verdict'],'identityOk':result['identityOk']},ensure_ascii=False),flush=True)
                page=context.pages[0] if context.pages else context.new_page()
                try:page.goto(launcher.TARGET_URL,wait_until='domcontentloaded',timeout=60000)
                except Exception:pass  # Keep the normal browser available for user verification.
                while not stopped[0] and context.pages:
                    active=next((p for p in context.pages if 'kalodata.com' in p.url),None)
                    challenge=False
                    if active:
                        try:challenge=active.title().strip().casefold() in ('just a moment...','请稍候…','请稍候...')
                        except Exception:pass
                    launcher.export_current_cookie(context)
                    changed=cookie_state()['updatedAt']!=cookie_at
                    if not identity_ready and changed and time.monotonic()-last_probe>=15:
                        result=probe(root);identity_ready=verified_probe(result)
                        cookie_at=cookie_state()['updatedAt'];last_probe=time.monotonic()
                    checkpoint(root,state,stage='identity_verified' if identity_ready else 'waiting_browser_verification' if challenge else 'waiting_login',
                               verificationRequired=challenge,identityOk=identity_ready,probeVerdict=result['verdict'])
                    for _ in range(6):
                        if stopped[0]:break
                        time.sleep(.5)
            finally:
                try:
                    if identity_ready:launcher.export_current_cookie(context)
                except Exception:pass
                context.close()
        checkpoint(root,state,stage='closed')
        return 0 if identity_ready else 1
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) and str(error).startswith('kalodata_') else type(error).__name__
        checkpoint(root,state,stage='failed',error=code)
        print(json.dumps({'error':code},ensure_ascii=False),flush=True)
        return 1
