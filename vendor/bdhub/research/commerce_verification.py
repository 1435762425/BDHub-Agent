"""复用同账号平台验证；仅返回验证后的会话，不发送或回放业务请求。"""
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from bdhub import config


def verify_session(transport, verification_header):
    from bdhub.enrich.pure_http_canary import account_stdin_payload
    from bdhub.hub.markets import get
    meta = get(transport.identity.market)
    identity = account_stdin_payload(transport.headers, device_id=transport.device_id,
                                    partner_id=transport.identity.partner_id)
    # 原会话收到的 Cookie 也属于本账号；不写回其它 Profile 或全局身份。
    cookies = dict(part.strip().split('=', 1) for part in identity['cookie_header'].split(';') if '=' in part)
    cookies.update(transport.session.cookies.get_dict())
    identity.update(cookie_header='; '.join(f'{k}={v}' for k, v in cookies.items()), fp=transport.fp,
                    api_host=meta.host, page_url=meta.warmup, aid=meta.aid,
                    user_language=meta.user_language, signer_region=meta.signer_region)
    process = subprocess.Popen([str(config.ROOT/'.venv/bin/python'), '-m', __name__], cwd=config.ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    try:
        stdout, _ = process.communicate(json.dumps({'identity':identity, 'verification':verification_header}), timeout=90)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise ValueError('commerce_verification_timeout') from None
    try:
        result = json.loads(stdout)
        transport.last_verification={k:result[k] for k in ('attempts','error_code') if k in result}
        if process.returncode or result.get('success') is not True or not isinstance(result.get('cookies'), dict) or not result.get('fp'):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('commerce_verification_required') from None
    existing = {}
    for name in list(transport.headers):
        if name.lower() == 'cookie':
            existing.update(dict(part.strip().split('=', 1) for part in transport.headers[name].split(';') if '=' in part))
            del transport.headers[name]
    existing.update(result['cookies'])
    transport.headers['cookie'] = '; '.join(f'{k}={v}' for k, v in existing.items())
    # Header和Session必须保持同一份已验证状态，避免下次验证被旧Cookie覆盖。
    transport.session.cookies.clear();transport.session.cookies.update(existing)
    transport.fp = result['fp']


def solve_bounded(client,verification,*,sleep=time.sleep):
    """仅重试验证步骤；认证拒绝、限流和不支持的挑战不继续尝试。"""
    for attempt in range(1,4):
        try:
            outcome=client._solve_captcha(verification,attempt)
            if outcome.get('success'):return {'success':True,'attempts':attempt}
            code='verification_not_passed'
        except Exception as exc:
            message=str(exc)
            match=re.fullmatch(r'slider_verify_failed_http_(\d{3}|invalid)_code_(-?\d{1,16}|invalid)',message)
            if match:
                code=match.group(0)
                if match[1] in {'401','403','429'}:return {'success':False,'attempts':attempt,'error_code':code}
            elif '不支持的验证码类型' in message:
                return {'success':False,'attempts':attempt,'error_code':'verification_type_unsupported'}
            else:code='verification_step_failed'
        if attempt<3:sleep(1)
    return {'success':False,'attempts':attempt,'error_code':code}


def main():
    from bdhub.enrich import pure_http_worker as worker, pure_http_worker_child as child
    request = json.load(sys.stdin)
    result = {'success':False}
    with tempfile.TemporaryDirectory(prefix='bdhub-commerce-verification-') as directory:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            client = probe = None
            try:
                runtime = worker.validate_runtime(worker._DEFAULT_RUNTIME)
                probe = child._load_runtime(runtime)
                identity = request['identity']
                child._configure_market_signer_runtime(probe, identity, directory)
                child._configure_market_captcha_runtime(probe, identity, directory)
                client = probe.PureHttpPartnerClient({'partner_id':identity['partner_id'], 'qps':1,
                    'trust_env':False, 'request_timeout_seconds':20}, identity, Path(directory))
                child._configure_market_transport(client, identity)
                verification = probe.parse_verify_header(request['verification'])
                outcome = solve_bounded(client,verification)
                result=outcome
                if outcome.get('success'):
                    result = outcome|{'cookies':client.session.cookies.get_dict(), 'fp':client.fp}
            except Exception:
                pass
            finally:
                if client:
                    client.session.close()
                if probe:
                    probe.signer_impl.reset_signer()
    # 由父进程捕获，禁止直接打印到任务日志。
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    main()
