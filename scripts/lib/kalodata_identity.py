"""Kalodata scraper identity.

The PID -> creator-lead source is served by the local Kalodata grabber, which logs in through a
Chrome extension: the extension's activation card is exchanged for a proxy and a session cookie,
and every later query reuses that cookie. This module is only the adapter; none of that flow is
reimplemented here.

Readiness is judged on the path that actually scrapes -- the same provider and endpoint the batch
worker uses. The grabber also ships ``account_canary.py``, but that checker additionally demands a
Cloudflare clearance cookie and reports "not ready" while real reads still succeed, so it is not
used as the verdict. A quota-exhausted answer is a *good* identity result: it proves the request
was authenticated.

Boundaries: the grabber owns its credential files. We report that the cookie exists and when it
last changed, never its contents, and we never copy it or the Chrome profile into this repository.
The activation card is a credential; the operator runs this system alone and asked for the card to
stay visible and editable in the frontend, so it lives in a 0600 file under ``config/`` and is
never logged.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

LEGACY = Path(__file__).resolve().parents[2].parent / '01-BDSystem-V2'
LOGIN_DIR_NAME = 'Kalodata登录器独立版'
DEFAULTS = {'version': 'kalodata-identity-v1', 'activationCode': '', 'canaryPid': ''}

# Probe verdicts. ``identityOk`` separates "the login works" from "we got data this second":
# an exhausted daily quota still proves the session was accepted.
VERDICTS = {'ready': True, 'quota_exhausted': True, 'auth_required': False,
            'business_rejected': None, 'unreachable': None}


def root_of(module_file=__file__):
    return Path(module_file).resolve().parents[2]


@lru_cache(maxsize=1)
def grabber():
    """The grabber project directory, read from the same config the Kalodata worker uses."""
    override = os.environ.get('KALODATA_PROJECT_DIR', '').strip()
    if override:
        return Path(override).expanduser()
    try:
        from lib.legacy_runtime import configure_vendored_bdhub
        configure_vendored_bdhub(root=root_of(),legacy_root=LEGACY)
        from bdhub import config
        return Path(config.load().kalodata.project_dir)
    except Exception:
        # A moved or unreadable legacy config must not make the whole module unusable.
        return Path('/Users/bjn00003/kalodatagrab/乘丰 对标查找')


def login_dir():
    return grabber() / LOGIN_DIR_NAME


def python_bin():
    return login_dir() / '.venv-mac/bin/python'


def cookie_path():
    return login_dir() / 'latest_cookie.txt'


def launcher_script():
    return login_dir() / 'cookie_launcher.py'


def config_path(root):
    return Path(root) / 'config/kalodata-identity.json'


def state_path(root):
    return Path(root) / 'var/kalodata-identity.json'


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError('kalodata_identity_invalid')
    # Never truncate a credential: a shortened card would fail later in a confusing way.
    code = str(raw.get('activationCode') or '').strip()
    if len(code) > 200 or any(character in code for character in '\r\n\t'):
        raise ValueError('kalodata_identity_code_invalid')
    pid = str(raw.get('canaryPid') or '').strip()
    # A product id is exactly 19 digits everywhere else in this system; anything else is a typo.
    if pid and not (pid.isdigit() and len(pid) == 19):
        raise ValueError('kalodata_identity_pid_invalid')
    return {'version': str(raw.get('version') or DEFAULTS['version']),
            'activationCode': code, 'canaryPid': pid}


def load(root=None):
    root = root or root_of()
    path = config_path(root)
    raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(DEFAULTS)
    return validate(raw)


def save(root, raw):
    """Validate first, then write atomically owner-only: the card is a credential."""
    config = validate(raw)
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)
    return config


def cookie_state():
    """Whether a session cookie exists and when it last changed -- never its contents."""
    try:
        stat = cookie_path().stat()
    except OSError:
        return {'present': False, 'updatedAt': None, 'bytes': 0}
    return {'present': stat.st_size > 0, 'updatedAt': stat.st_mtime, 'bytes': stat.st_size}


def proxy_state():
    path = login_dir() / 'extension_runtime.json'
    if not path.exists():
        return {'configured': False, 'updatedAt': None}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        configured = bool(str(payload.get('proxy_url') or '').strip())
    except (OSError, ValueError):
        return {'configured': False, 'updatedAt': None}
    return {'configured': configured, 'updatedAt': path.stat().st_mtime}


def export_helpers():
    """Import the grabber's own launcher module so its functions can be reused verbatim."""
    directory = str(login_dir())
    if directory not in sys.path:
        sys.path.insert(0, directory)
    import cookie_launcher
    return cookie_launcher


def probe_pid(root, config):
    """Prefer the configured pid, else a real product from the current collection."""
    if config['canaryPid']:
        return config['canaryPid']
    db = Path(root) / 'var/global-source.sqlite'
    if not db.exists():
        return ''
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            row = conn.execute("SELECT r.id FROM global_source_run r WHERE r.state='completed' "
                               "AND (SELECT count(*) FROM global_source_product p WHERE p.run_id=r.id)>0 "
                               "ORDER BY r.created DESC LIMIT 1").fetchone()
            if not row:
                return ''
            pid = conn.execute('SELECT pid FROM global_source_product WHERE run_id=? ORDER BY pid LIMIT 1',
                               (row[0],)).fetchone()
        return pid[0] if pid else ''
    except sqlite3.Error:
        return ''


def read_state(root):
    path = state_path(root)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_state(root, payload):
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def probe(root=None, pid=None, clock=time.time):
    """Ask the production path for one creator page and classify what came back.

    This is a real read against Kalodata on the same provider and endpoint the batch worker uses,
    so its verdict is the one that matters. It does not write to the platform.
    """
    root = Path(root or root_of())
    sys.path.insert(0, str(root / 'scripts'))
    from lib.batch_source_runtime import kalodata_provider
    from lib.cycle_kalodata import PATH, CycleError
    config = load(root)
    target = pid or probe_pid(root, config)
    if not target:
        raise ValueError('kalodata_probe_pid_missing')
    end = date.today()
    payload = {'startDate': (end - timedelta(days=13)).isoformat(), 'endDate': end.isoformat(),
               'authority': True, 'pageSize': 50, 'pageNo': 1,
               'sort': [{'field': 'revenue', 'type': 'DESC'}], 'id': target}
    started = clock()
    verdict, detail, rows = 'unreachable', None, None
    proxy_configured = False
    try:
        with kalodata_provider(root) as provider:
            proxy_configured = bool(provider.proxy)
            try:
                body = provider.request(PATH, payload)
            except CycleError as error:
                code = str(error)
                verdict = ('quota_exhausted' if code == 'kalodata_daily_quota_exhausted'
                           else 'auth_required' if code == 'kalodata_auth_required'
                           else 'business_rejected' if code == 'kalodata_business_rejected'
                           else 'unreachable')
                detail = code
            else:
                data = body.get('data') if isinstance(body, dict) else None
                rows = len(data.get('list') or []) if isinstance(data, dict) else 0
                verdict, detail = 'ready', 'ok'
    except Exception as error:
        detail = type(error).__name__
    result = {'verdict': verdict, 'identityOk': VERDICTS.get(verdict), 'detail': detail,
              'pid': target, 'rows': rows, 'proxyConfigured': proxy_configured,
              'checkedAt': started, 'elapsedSeconds': round(clock() - started, 2)}
    _write_state(root, result)
    return result


def login_state_path(root):
    return Path(root) / 'var/kalodata-login.json'


def login_state(root=None):
    """Whether a login Chrome is open, so the page never claims the flow finished early."""
    root = Path(root or root_of())
    path = login_state_path(root)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    pid = state.get('pid')
    alive = False
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
    return state | {'running': alive}


def runner_ready():
    """The grabber's own interpreter, where Playwright and curl_cffi live."""
    return python_bin().is_file()


def status(root=None):
    """Cheap state for polling: reads files only, never contacts Kalodata."""
    root = Path(root or root_of())
    config = load(root)
    return {'config': config, 'grabber': str(grabber()), 'loginDir': str(login_dir()),
            'pythonReady': runner_ready(), 'cookie': cookie_state(), 'proxy': proxy_state(),
            'lastProbe': read_state(root), 'probePid': probe_pid(root, config),
            'login': login_state(root), 'probeEndpoint': 'kalodata',
            'probeHint': '测试身份会向 Kalodata 发一次真实达人列表请求；额度用尽的回答也算身份正常。'}


def start_login(root, mode, *, code='', clock=time.time):
    """Open the grabber's dedicated Chrome for activation or refresh; it closes with the window."""
    root = Path(root)
    if not runner_ready():
        raise ValueError('kalodata_runtime_missing')
    if mode not in ('activate', 'refresh'):
        raise ValueError('kalodata_login_mode_invalid')
    current = login_state(root)
    if current and current.get('running'):
        raise ValueError('kalodata_login_already_running')
    log_path = root / 'var/kalodata-login.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if mode == 'activate':
        runner = [str(python_bin()), str(launcher_script())]
        environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'KALODATA_ACTIVATION_CODE': code}
    else:
        runner = [str(python_bin()), str(root / 'scripts/kalodata-extension-refresh.py')]
        environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    with log_path.open('a', encoding='utf-8') as log:
        log.write(f"\n=== {mode} 开始 {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        log.flush()
        child = subprocess.Popen(runner, cwd=str(login_dir()), stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                 env=environment)
    state = {'mode': mode, 'pid': child.pid, 'startedAt': clock(), 'log': str(log_path)}
    login_state_path(root).write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return state | {'running': True}
