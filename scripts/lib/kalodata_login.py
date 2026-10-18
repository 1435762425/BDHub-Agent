"""Use the existing extension login without overwriting its newly issued identity."""
import json
import os
from pathlib import Path
import signal
import time


def initialize_session(launcher,context,mode,code=''):
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
        raise ValueError('kalodata_proxy_export_failed')
    launcher.export_current_cookie(context)
    return extension_id


def checkpoint(root,state,**details):
    from lib.kalodata_identity import login_state_path
    target=login_state_path(root);target.parent.mkdir(parents=True,exist_ok=True)
    value={**state,**details,'pid':os.getpid(),'checkedAt':time.time()}
    tmp=target.with_name(target.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(target)


def run_login(root,mode):
    from lib.kalodata_identity import export_helpers,load
    launcher=export_helpers();code=os.environ.get('KALODATA_ACTIVATION_CODE') or load(root)['activationCode']
    state={'mode':mode,'startedAt':time.time(),'log':str(Path(root)/'var/kalodata-login.log')}
    stopped=[False]
    def stop(*_):stopped[0]=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    checkpoint(root,state,stage='extension_login')
    try:
        with launcher.sync_playwright() as playwright:
            context=launcher.launch_context(playwright)
            try:
                initialize_session(launcher,context,mode,code)
                checkpoint(root,state,stage='identity_exported')
                print('扩展新身份及匹配代理已同步；不会重新注入旧 Cookie。',flush=True)
                page=context.pages[0] if context.pages else context.new_page()
                try:page.goto(launcher.TARGET_URL,wait_until='domcontentloaded',timeout=60000)
                except Exception:pass  # Keep the normal browser available for user verification.
                while not stopped[0] and context.pages:
                    active=next((p for p in context.pages if 'kalodata.com' in p.url),None)
                    challenge=False
                    if active:
                        try:challenge=active.title().strip().casefold() in ('just a moment...','请稍候…','请稍候...')
                        except Exception:pass
                    checkpoint(root,state,stage='waiting_browser_verification' if challenge else 'browser_open',
                               verificationRequired=challenge)
                    launcher.export_current_cookie(context)
                    for _ in range(6):
                        if stopped[0]:break
                        time.sleep(.5)
            finally:
                try:launcher.export_current_cookie(context)
                except Exception:pass
                context.close()
        checkpoint(root,state,stage='closed')
        return 0
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) and str(error).startswith('kalodata_') else type(error).__name__
        checkpoint(root,state,stage='failed',error=code)
        print(json.dumps({'error':code},ensure_ascii=False),flush=True)
        return 1
