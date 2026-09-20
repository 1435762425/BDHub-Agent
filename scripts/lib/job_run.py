"""Start one long-running catalogue job detached, and report whether it is still running.

Both catalogue actions are real platform work -- selecting products and creating TapLinks -- so they
follow the same shape as the Kalodata login: the request returns immediately, the work runs in its
own process, and the page follows a small state file instead of pretending the call finished. The
underlying scripts already own their own ledgers, receipts and resume behaviour; this module only
starts them and reports liveness.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Only jobs with a verified, resumable script are listed. A job without an entry has no button.
JOBS = {
    'selection': {'label': '全托商品选入', 'log': 'selection-run.log', 'config': 'selection-run.json'},
    'links': {'label': '链接准备', 'log': 'link-prepare-run.log', 'config': 'link-prepare-run.json'},
    # 渠道由**作业名**决定而不是配置项：两条渠道共用一份会被合并的配置时，先跑非全托、再点全托的
    # "准备并新建"，route=campaign 会被带过去——那是真能建错链的隐患，所以各用一份状态。
    'linksCampaign': {'label': '非全托链接准备', 'log': 'link-prepare-campaign-run.log',
                      'config': 'link-prepare-campaign-run.json'},
    # 非全托采集：加入新活动之后要重新读一遍活动与商品，否则新活动的商品永远进不了池子。
    # 只读平台（platformWrites 恒为 false），采集完顺带按当前门槛重新筛分。
    'campaignCollect': {'label': '非全托采集', 'log': 'campaign-collect-run.log',
                        'config': 'campaign-collect-run.json'},
    # Identity backfill reads and writes nothing on the platform; it only resolves handles to OECIDs.
    'identity': {'label': '达人身份（OECID）', 'log': 'identity-run.log', 'config': 'identity-run.json'},
    # 收信监控：**只读**轮询已索引的会话，把达人回复与橱窗通知落进 inbox_event。它不是新写的轮询器
    # ——脚本早就在跑（`poll-cycle-inbox.py`），这里只是让它进作业面板：能看状态、能手动启停、
    # 定时是可选的开关。它和补身份/链接抢同一把 ACC6 live 锁，靠退避让路（不设互斥：监控本来就该
    # 一直开着，把它设成互斥等于让收信停摆）。
    'inbox': {'label': '收信监控', 'log': 'cycle-inbox-run.log', 'config': 'cycle-inbox.json'},
    'agentReply': {'label': 'Agent 集中回复', 'log': 'agent-reply-run.log', 'config': 'agent-reply-run.json'},
}

# 链接作业互斥：两者会开同一个账号的会话，也写同一份链接账本。
LINK_JOBS = ('links', 'linksCampaign')

SELECTION_MAX = 600
ALLOWED = {'selection': {'limit'}, 'links': {'readLimit', 'creates', 'lanes', 'qps'},
           'linksCampaign': {'readLimit', 'creates', 'lanes', 'qps'},
           'campaignCollect': {'maxRequests', 'passes'},
           'identity': {'batchSize', 'cohortSize'},
           'inbox': {'limit', 'interval'}}
ALLOWED['agentReply']={'interval'}
LANES = (1, 3, 6, 9)
QPS = (3, 5, 8, 12)
READ_MAX = 200
CREATES_MAX = 200
# The Find worker accepts 1, 10, 20 or 50 per round, but 1 skips the cohort runner and reports no
# target count. 50 keeps the published 12 QPS / 9-lane policy and only amortizes fixed startup cost.
COHORTS = (10, 20, 50)
IDENTITY_MAX = 200000
# 收信每一轮最多核验几个会话、两轮之间停多久。上限沿用脚本自己的取值域（1..12、>=30 秒）：
# 收信吃的是 ACC6 的 live 锁，轮次开大只会把补身份/发送挤得更久。
INBOX_LIMIT_MAX = 12
INBOX_INTERVAL = (30, 3600)


def state_path(root, name):
    return Path(root) / f'var/job-{name}.json'


def config_path(root, name):
    return Path(root) / 'config' / JOBS[name]['config']


def log_path(root, name):
    return Path(root) / 'var' / JOBS[name]['log']


def progress_path(root, name):
    """Where a running job publishes the step it is on, so the page shows real movement."""
    if name == 'inbox':
        # 收信监控本来就在写这份状态（页面也一直在读它）；再写一份就成了两个真相。
        return Path(root) / 'var/cycle-inbox-status.json'
    if name == 'agentReply':
        return Path(root) / 'var/agent-reply-status.json'
    return Path(root) / f'var/job-{name}-progress.json'


def stop_path(root, name):
    """The stop request. A file rather than a signal: the driver decides its own safe exit point."""
    return Path(root) / f'var/job-{name}.stop'


def request_stop(root, name):
    """Ask a running job to end at its next safe point. Refuses when nothing is running."""
    if name not in JOBS:
        raise ValueError('job_unknown')
    current = state(root, name)
    if not current or not current.get('running'):
        raise ValueError('job_not_running')
    path = stop_path(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'name': name, 'requestedAt': time.time()}, ensure_ascii=False) + '\n',
                    encoding='utf-8')
    return state(root, name)


def read_progress(root, name):
    """The published step, or None. A torn or hand-edited file is simply no progress.

    Each job publishes the step that makes sense for it, so the file is normalized per job: the
    link driver reports passes and ledger states, the identity driver reports rounds and handles.
    """
    path = progress_path(root, name)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        if name == 'identity':
            return _identity_step(payload)
        if name == 'campaignCollect':
            return _collect_step(payload)
        if name == 'inbox':
            return _inbox_step(payload)
        if name == 'agentReply':
            return {'state':str(payload.get('state') or ''),'claimed':int(payload.get('claimed') or 0),
                    'prepared':int(payload.get('prepared') or 0),'human':int(payload.get('human') or 0),
                    'confirmed':int(payload.get('confirmed') or 0),'unknown':int(payload.get('unknown') or 0),
                    'error':str(payload['error']) if payload.get('error') else None}
        return _link_step(payload)
    except (TypeError, ValueError):
        return None


def _link_step(payload):
    states = payload.get('states')
    clean = {str(key): int(value) for key, value in states.items()} if isinstance(states, dict) else {}
    return {'phase': str(payload.get('phase') or ''), 'step': int(payload.get('step') or 0),
            'pass': int(payload['pass']) if isinstance(payload.get('pass'), int) else None,
            'passes': int(payload.get('passes') or 0), 'created': int(payload.get('created') or 0),
            'updatedAt': float(payload.get('updatedAt') or 0),
            'startedAt': float(payload.get('startedAt') or 0),
            'total': int(payload.get('total') or 0), 'states': clean}


def _collect_step(payload):
    """非全托采集发布的步骤：读到第几轮、花了多少请求、拿了多少 offer。"""
    return {'startedAt': float(payload.get('startedAt') or 0),
            'updatedAt': float(payload.get('updatedAt') or 0),
            'status': str(payload.get('status') or ''),
            'round': int(payload.get('round') or 0), 'rounds': int(payload.get('rounds') or 0),
            'requests': int(payload.get('requests') or 0), 'offers': int(payload.get('offers') or 0),
            'runFile': str(payload.get('runFile') or ''),
            'error': str(payload['error']) if payload.get('error') else None}


def _identity_step(payload):
    def whole(key):
        return int(payload.get(key) or 0)
    # 停下来的**原因**要一起发布：只写一个 `internal_error`，操作者什么也做不了。驱动抓到的
    # traceback 尾巴就是"到底哪一句炸了"；只留最近两条、每条截断，够定位又不把日志灌进页面。
    errors = []
    for row in payload.get('errors') or []:
        if not isinstance(row, dict):
            continue
        detail = str(row.get('detail') or '').strip()
        if detail:
            errors.append({'round': int(row.get('round') or 0), 'detail': detail[-1200:]})
    return {'startedAt': float(payload.get('startedAt') or 0),
            'updatedAt': float(payload.get('updatedAt') or 0),
            'rounds': whole('rounds'), 'limit': whole('limit'), 'cohortSize': whole('cohortSize'),
            'pendingAtStart': whole('pendingAtStart'), 'pending': whole('pending'),
            'claimed': whole('claimed'), 'lastTargets': whole('lastTargets'),
            'found': whole('found'), 'notFound': whole('notFound'),
            # 正在跑第几轮＋这一轮何时开始：一轮几十秒，没有它进度条就是一动不动。
            'roundRunning': whole('roundRunning'),
            'roundStartedAt': float(payload['roundStartedAt']) if isinstance(payload.get('roundStartedAt'), (int, float)) else None,
            'stopReason': str(payload['stopReason']) if payload.get('stopReason') else None,
            # 别人占着那个库时驱动会重试；页面上必须能看出"在等"和"卡死"的区别。
            'retry': whole('retry'),
            'errors': errors[-2:]}


def _inbox_step(payload):
    """收信监控发布的是一轮轮询的结果：读了多少会话、新进多少事件、为什么停下。

    只读作业，所以这里没有写入计数；`errorCode` 就是"为什么这一轮没读到东西"（最常见的是
    `live_guard_busy`——补身份/发送正占着同一把 ACC6 live 锁，它在退避重试，不是坏了）。
    """
    def whole(key):
        return int(payload.get(key) or 0)
    status = payload.get('status') if isinstance(payload.get('status'), dict) else {}
    return {'processed': whole('processed'), 'added': whole('added'), 'historical': whole('historical'),
            'liveReplies': whole('liveReplies'), 'indexedTargets': whole('indexedTargets'),
            'serviceDecisions': whole('serviceDecisions'),
            'errorCode': str(payload['errorCode']) if payload.get('errorCode') else None,
            'state': str(payload.get('state') or ''),
            'checkedAt': float(payload.get('checkedAt') or 0),
            'conversations': int(status.get('conversations') or 0),
            'events': int(status.get('events') or 0),
            'historicalEvents': int(status.get('historicalEvents') or 0),
            'gaps': int(status.get('gaps') or 0),
            'pendingContent': int(status.get('pendingContent') or 0),
            'lastCheckedAt': float(status.get('lastCheckedAt') or 0),
            'automaticRepliesEnabled': status.get('automaticRepliesEnabled') is True}


def python_bin(root):
    return Path(root) / '.venv/bin/python'

def _bounded(value, low, high, code):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(code)
    return value


def validate(name, raw):
    if name not in JOBS or not isinstance(raw, dict):
        raise ValueError('job_unknown')
    # A misspelled key would otherwise be silently ignored and the job would run with defaults.
    if set(raw) - ALLOWED[name]:
        raise ValueError('job_unknown_field')
    if name == 'selection':
        return {'limit': _bounded(raw.get('limit', SELECTION_MAX), 1, SELECTION_MAX, 'job_limit_invalid')}
    if name == 'campaignCollect':
        # 采集是只读的，但一次读几十个活动仍要花几分钟：每轮与总轮数都可调。
        return {'maxRequests': _bounded(raw.get('maxRequests', 150), 1, 150, 'job_collect_requests_invalid'),
                'passes': _bounded(raw.get('passes', 40), 1, 200, 'job_collect_passes_invalid')}
    if name == 'identity':
        cohort = raw.get('cohortSize', 50)
        if cohort not in COHORTS:
            raise ValueError('job_cohort_invalid')
        # A ceiling on handles attempted, not a target: a short backlog makes a short run.
        return {'batchSize': _bounded(raw.get('batchSize', 2000), 1, IDENTITY_MAX, 'job_batch_invalid'),
                'cohortSize': cohort}
    if name == 'inbox':
        # 只读监控：一轮核验几个会话 + 两轮之间停多久。它不发送，所以这里没有写入开关。
        return {'limit': _bounded(raw.get('limit', 6), 1, INBOX_LIMIT_MAX, 'job_inbox_limit_invalid'),
                'interval': _bounded(raw.get('interval', 60), INBOX_INTERVAL[0], INBOX_INTERVAL[1],
                                     'job_inbox_interval_invalid')}
    if name == 'agentReply':
        return {'interval':_bounded(raw.get('interval',60),30,3600,'job_agent_reply_interval_invalid')}
    lanes = raw.get('lanes', 9)
    qps = raw.get('qps', 12)
    if lanes not in LANES or qps not in QPS:
        raise ValueError('job_pacing_invalid')
    return {'readLimit': _bounded(raw.get('readLimit', 15), 1, READ_MAX, 'job_read_invalid'),
            # 0 means read and judge only. Creating a link is a platform write, so it is opt-in.
            'creates': _bounded(raw.get('creates', 0), 0, CREATES_MAX, 'job_creates_invalid'),
            # Same-account HTTP lanes. One lane was the reason creation ran far below its measured
            # rate: the create step supports the full set, the driver simply never passed it.
            'lanes': lanes, 'qps': qps}


def config_problem(root, name):
    """Whether the stored config file cannot be used as written.

    A rule can tighten after a file was written -- exactly what happened when the identity cohort
    sizes were narrowed -- and the stored value then fails validation. That must not take down the
    whole status call: every job panel would go dark because one file is stale. The defaults are
    used instead, and the fact is reported so the page can say so.
    """
    path = config_path(root, name)
    if not path.exists():
        return False
    try:
        validate(name, json.loads(path.read_text(encoding='utf-8')))
        return False
    except (OSError, ValueError):
        return True


def load_config(root, name):
    path = config_path(root, name)
    raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    try:
        return validate(name, raw)
    except ValueError:
        return validate(name, {})


def save_config(root, name, raw):
    config = validate(name, raw)
    path = config_path(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
    return config


def state(root, name, *, clock=time.time):
    """Whether the job is running right now, judged by the process, not by a stale flag."""
    path = state_path(root, name)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    pid = payload.get('pid')
    alive = False
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
    return payload | {'running': alive, 'stopping': stop_path(root, name).exists(),
                      'progress': read_progress(root, name)}


def command_for(root, name, config, stamp):
    root = Path(root)
    if name == 'selection':
        return [str(python_bin(root)), str(root / 'scripts/select-global-products.py'), 'execute',
                '--limit', str(config['limit'])]
    if name == 'campaignCollect':
        return [str(python_bin(root)), str(root / 'scripts/campaign-collect.py'),
                '--max-requests', str(config['maxRequests']), '--passes', str(config['passes']),
                '--screen',
                '--progress', str(progress_path(root, name)),
                '--stop', str(stop_path(root, name)),
                '--report', str(root / f'var/campaign-collect-{stamp}.json')]
    if name == 'identity':
        return [str(python_bin(root)), str(root / 'scripts/identity-batch.py'),
                '--limit', str(config['batchSize']), '--cohort-size', str(config['cohortSize']),
                '--progress', str(progress_path(root, name)),
                '--stop', str(stop_path(root, name)),
                '--report', str(root / f'var/identity-run-{stamp}.json')]
    if name == 'inbox':
        # 只读轮询，没有平台写入，所以这里的 config 只有节奏。状态文件沿用脚本自己的
        # `var/cycle-inbox-status.json`（页面本来就在读它），不另写一份进度文件。
        return [str(python_bin(root)), str(root / 'scripts/poll-cycle-inbox.py'), '--worker',
                '--limit', str(config['limit']), '--interval', str(config['interval']),
                '--stop', str(stop_path(root, name))]
    if name == 'agentReply':
        return [str(python_bin(root)),str(root/'scripts/run-agent-replies.py'),'--worker',
                '--interval',str(config['interval']),'--stop',str(stop_path(root,name))]
    report = root / f'var/catalog-link-run-{stamp}.json'
    # 非全托：目标来自已入池商品，读卡按活动逐个进行；同一套驱动器，只换渠道。
    route = 'campaign' if name == 'linksCampaign' else 'selected'
    return [str(python_bin(root)), str(root / 'scripts/catalog-link-batch.py'),
            '--route', route,
            '--limit', str(config['readLimit']), '--passes', '80', '--creates', str(config['creates']),
            '--lanes', str(config['lanes']), '--qps', str(config['qps']),
            '--progress', str(progress_path(root, name)),
            '--seed', '--report', str(report)]


def start(root, name, raw=None, *, clock=time.time, spawn=subprocess.Popen):
    """Launch the job detached. Refuses to start a second copy of the same job."""
    root = Path(root)
    if name not in JOBS:
        raise ValueError('job_unknown')
    current = state(root, name)
    if current and current.get('running'):
        raise ValueError('job_already_running')
    # 两条链接作业互斥：它们会开同一个账号的会话、写同一份链接账本。让第二个直接说"已经在跑"，
    # 而不是让它进去撞账号锁、报一个看不出原因的错。
    if name in LINK_JOBS:
        for sibling in LINK_JOBS:
            if sibling != name and (state(root, sibling) or {}).get('running'):
                raise ValueError('job_already_running')
    config = validate(name, {**load_config(root, name), **(raw or {})})
    save_config(root, name, config)
    script_root = str(root / 'scripts')
    if script_root not in sys.path:
        sys.path.insert(0, script_root)
    log = log_path(root, name)
    log.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    with log.open('a', encoding='utf-8') as handle:
        handle.write(f"\n=== {name} 开始 {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        handle.flush()
        child = spawn(command_for(root, name, config, stamp), cwd=str(root), stdin=subprocess.DEVNULL,
                      stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
                      env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    record = {'name': name, 'label': JOBS[name]['label'], 'pid': child.pid, 'startedAt': clock(),
              'config': config, 'log': str(log),
              'platformWrites': name == 'agentReply' or (name in LINK_JOBS and config['creates'] > 0)}
    # Clear the previous run's step and any stop request: the new process publishes its own step,
    # and a leftover stop file would end this run before its first round.
    for stale in (progress_path(root, name), stop_path(root, name)):
        try:
            stale.unlink()
        except OSError:
            pass
    state_path(root, name).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if name == 'agentReply' and (root / 'scripts/operations-scheduler.py').is_file():
        from lib.operations_scheduler import scheduler_state, start_scheduler
        if not scheduler_state(root)['running']:
            start_scheduler(root)
    return record | {'running': True, 'stopping': False}


def status(root, name=None):
    names = [name] if name else sorted(JOBS)
    return {key: {'label': JOBS[key]['label'], 'config': load_config(root, key), 'run': state(root, key),
                  'configInvalid': config_problem(root, key)}
            for key in names}
