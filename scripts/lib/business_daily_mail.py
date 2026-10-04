"""Durable email delivery for frozen daily reports, separate from TikTok execution."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import smtplib
import ssl
import time
import certifi

from lib.business_daily import collect, render
from lib.cycle_stats import BEIJING

EMAIL = re.compile(r'[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+')


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as out:
        os.chmod(temporary, 0o600)
        json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write('\n')
    temporary.replace(path)


@contextmanager
def locked(root):
    path = Path(root) / 'var/business-daily/runner.lock'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a') as handle:
        os.chmod(path, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def configuration(root):
    path = Path(root) / 'private-data/business-daily-mail.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    recipients = value.get('recipients')
    if not isinstance(recipients, list) or not recipients or len(recipients) > 10 or \
            any(not isinstance(x, str) or not EMAIL.fullmatch(x) for x in recipients):
        raise ValueError('mail_recipients_invalid')
    schedule = value.get('sendTime', '09:00')
    if not isinstance(schedule, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', schedule):
        raise ValueError('mail_send_time_invalid')
    if value.get('timezone', 'Asia/Shanghai') != 'Asia/Shanghai':
        raise ValueError('mail_timezone_invalid')
    if type(value.get('enabled')) is not bool:
        raise ValueError('mail_enabled_invalid')
    smtp = value.get('smtp', {})
    if not isinstance(smtp, dict):
        raise ValueError('mail_smtp_invalid')
    sender = value.get('sender', '')
    if sender and not EMAIL.fullmatch(sender):
        raise ValueError('mail_sender_invalid')
    for field in ('host', 'username'):
        text = smtp.get(field, '')
        if not isinstance(text, str) or len(text) > 254 or any(c in text for c in '\r\n\x00'):
            raise ValueError('mail_smtp_invalid')
    mode = smtp.get('security', 'ssl')
    port = smtp.get('port', 465)
    if mode not in ('ssl', 'starttls') or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('mail_tls_invalid')
    password_file = smtp.get('passwordFile', 'private-data/business-daily-mail.password')
    secret = (Path(root) / password_file).resolve()
    if not secret.is_relative_to((Path(root) / 'private-data').resolve()):
        raise ValueError('mail_secret_path_invalid')
    return value


def credentials(root, config):
    smtp = config.get('smtp', {})
    path = Path(root) / smtp.get('passwordFile', 'private-data/business-daily-mail.password')
    if not config.get('sender') or not smtp.get('host') or not smtp.get('username') or not path.is_file():
        return None
    password = path.read_text().strip()
    if smtp.get('host') == 'smtp.gmail.com':
        password = ''.join(password.split())  # Google displays app passwords in spaced groups.
    return password if password else None


def report_paths(root, day):
    folder = Path(root) / 'var/business-daily/reports' / day
    return folder, folder / 'report.json'


def capture(root, day, *, now=None, collector=collect):
    now = time.time() if now is None else now
    folder, path = report_paths(root, day)
    if path.is_file():
        return json.loads(path.read_text())
    report = collector(root, day, now=now)
    subject, plain, html = render(report)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, body in (('email.txt', plain), ('email.html', html)):
        file = folder / name
        file.write_text(body, encoding='utf-8')
        os.chmod(file, 0o600)
    # Publish the immutable report last. A crash before here only leaves regenerable rendered files.
    save_json(path, report)
    return report


def _message(root, day, config, now):
    folder, path = report_paths(root, day)
    report = json.loads(path.read_text())
    subject, plain, html = render(report)
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = config['sender']
    message['To'] = ', '.join(config['recipients'])
    message['Date'] = format_datetime(datetime.fromtimestamp(now, BEIJING))
    identity = hashlib.sha256((str(Path(root).resolve()) + '\0' + day).encode()).hexdigest()[:24]
    domain = config['sender'].split('@', 1)[1]
    message['Message-ID'] = f'<bdhub-daily-{identity}@{domain}>'
    message.set_content(plain)
    message.add_alternative(html, subtype='html')
    return message


def deliver(root, day, config, *, now=None, smtp_factory=None):
    """Call under the runner lock. No retry once DATA might have been submitted."""
    now = float(time.time() if now is None else now)
    state_path = Path(root) / 'var/business-daily/delivery' / (day + '.json')
    state = json.loads(state_path.read_text()) if state_path.is_file() else {'date': day, 'attempts': 0}
    if state.get('state') in ('sent', 'submitting', 'unknown'):
        return {'state': state['state'], 'date': day, 'duplicatePrevented': True}
    if state['attempts'] >= 3 or state.get('nextAttempt', 0) > now:
        return {'state': 'waiting_retry' if state['attempts'] < 3 else 'failed', 'date': day}
    password = credentials(root, config)
    if not password:
        return {'state': 'waiting_mail_configuration', 'date': day, 'emailSent': False}
    message = _message(root, day, config, now)
    smtp = config['smtp']
    state['attempts'] += 1
    client = None
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        factory = smtp_factory or (smtplib.SMTP_SSL if smtp.get('security', 'ssl') == 'ssl' else smtplib.SMTP)
        client = factory(smtp['host'], smtp.get('port', 465), timeout=30,
                         **({'context': context} if smtp.get('security', 'ssl') == 'ssl' else {}))
        if smtp.get('security', 'ssl') == 'starttls':
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(smtp['username'], password)
    except (OSError, smtplib.SMTPException):
        if client:
            client.close()
        state.update(state='failed_before_submission', updatedAt=now, nextAttempt=now + 900)
        save_json(state_path, state)
        return {'state': state['state'], 'date': day, 'emailSent': False}
    try:
        state.update(state='submitting', updatedAt=now, messageId=message['Message-ID'],
                     recipientHash=hashlib.sha256('\0'.join(config['recipients']).encode()).hexdigest())
        # Mark before the first possible DATA submission; a killed process must not blindly resend.
        save_json(state_path, state)
        refused = client.send_message(message)
        if refused:
            state.update(state='unknown', updatedAt=time.time(), reason='partial_recipient_result')
        else:
            state.update(state='sent', updatedAt=time.time(), smtpAccepted=True)
        save_json(state_path, state)
        return {'state': state['state'], 'date': day, 'emailSent': state['state'] == 'sent',
                'smtpAccepted': state['state'] == 'sent'}
    except (OSError, smtplib.SMTPException):
        state.update(state='unknown', updatedAt=time.time(), reason='submission_result_unknown')
        save_json(state_path, state)
        return {'state': 'unknown', 'date': day, 'emailSent': False}
    finally:
        if client:
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                client.close()


def tick(root, *, now=None, collector=collect, smtp_factory=None):
    """Midnight captures yesterday's report; due-time ticks send its frozen tables once."""
    now = float(time.time() if now is None else now)
    config = configuration(root)
    if not config['enabled']:
        return {'state': 'disabled', 'emailSent': False}
    local = datetime.fromtimestamp(now, BEIJING)
    day = (local.date() - timedelta(days=1)).isoformat()
    with locked(root):
        report = capture(root, day, now=now, collector=collector)
        hour, minute = map(int, config.get('sendTime', '09:00').split(':'))
        if (local.hour, local.minute) < (hour, minute):
            return {'state': 'waiting_send_time', 'date': day, 'emailSent': False}
        return deliver(root, day, config, now=now, smtp_factory=smtp_factory)


def launchagent(root, python):
    """Project-local recurring job; has no dependency on the business scheduler or its controls."""
    return {'Label': 'io.bdhub.agent.business-daily',
            'ProgramArguments': [str(python), str(Path(root) / 'scripts/business-daily.py'), 'tick'],
            'WorkingDirectory': str(root), 'RunAtLoad': True, 'StartInterval': 60,
            'StandardOutPath': str(Path(root) / 'var/business-daily/runner.log'),
            'StandardErrorPath': str(Path(root) / 'var/business-daily/runner-error.log'),
            'ProcessType': 'Background'}
