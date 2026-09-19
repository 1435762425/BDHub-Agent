"""The operating jobs this workbench can run, and the operator's schedule intent for each.

The operator asked for every job to be startable by hand and for scheduling to be optional and
visible, never the only entry point. So this module keeps two things apart:

* what a job can actually do right now -- ``manual`` says whether a manual trigger exists and
  which endpoint serves it. A job with no verified trigger says so instead of offering a button
  that would pretend.
* what the operator wants scheduled -- ``config/jobs.json``. Every schedule is off by default and
  the timer itself is not built yet, so the page must label the switch as recorded intent rather
  than a running schedule. Nothing here starts a process.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

DEFAULTS = {'version': 'jobs-v2', 'jobs': {}}

# Manual triggers exist only where a verified path already backs them. Everything else reports
# 'unwired' so the page never invents a button.
JOBS = (
    {'id': 'catalog_collect', 'name': '商品发现 / 刷新', 'group': '货盘',
     'description': '按“高机会商品 → 仅全球销售商品”逐页采集，可随时停下再继续。',
     'manual': 'global-source-sync', 'defaultAt': '03:00'},
    {'id': 'catalog_screen', 'name': '商品筛分', 'group': '货盘',
     'description': '按入池门槛筛出合格商品并记账；采集完成后已自动执行一次。',
     'manual': 'catalog-screen-run', 'defaultAt': '03:30'},
    {'id': 'creator_leads', 'name': '达人线索查询（Kalodata）', 'group': '达人',
     'description': '按 PID 查可推广达人；有每日额度，用完需等待。',
     'manual': 'unwired', 'defaultAt': '04:00'},
    {'id': 'creator_profile_refresh', 'name': '达人档案刷新', 'group': '达人',
     'description': '按 OECID 刷新平台画像；单品入口在“稳定身份”页。',
     'manual': 'unwired', 'defaultAt': '04:30'},
    {'id': 'link_prepare', 'name': 'TapLink 链接准备', 'group': '货盘',
     'description': '先查已有链接并复用，确认缺链才按已确认分佣新建。',
     'manual': 'unwired', 'defaultAt': '05:00'},
    {'id': 'campaign_material_refresh', 'name': 'Campaign 来源与 TapLink 日检', 'group': '货盘',
     'description': '每日先完整刷新 Campaign 与资格，再只读核验对应标准 TapLink；不建链、不删卡。',
     'manual': 'unwired', 'defaultAt': '03:00', 'cadence': 'daily'},
    {'id': 'selected_taplink_verify', 'name': '全托已选 TapLink 周检', 'group': '货盘',
     'description': '每周统一只读核验全托已选商品的当前标准 TapLink；不建链、不删卡。',
     'manual': 'unwired', 'defaultAt': '05:00', 'cadence': 'weekly', 'defaultWeekday': 0},
    # 收信监控是**只读**的：它不发送，只把达人回复与橱窗通知落库。它和补身份/发送抢同一把账号
    # live 锁，抢不到就退避重试——所以它适合开成常驻，不适合当唯一入口。
    {'id': 'inbox_monitor', 'name': '收信监控（回复与橱窗）', 'group': '达人',
     'description': '只读轮询已索引会话，记录达人回复、橱窗通知与待人工事项；不发消息。',
     # 这里是**定时意向**，不是启停入口：收信监控的启停放在「监控与回复」卡片上（那里同时能看到
     # 读数和"为什么这一轮没读到"）。所以保持 unwired——不是没接上，是不给它放第二个入口。
     'manual': 'unwired', 'defaultAt': '09:00'},
)

MANUAL_ENDPOINTS = {'global-source-sync': '/api/global-source',
                    'catalog-screen-run': '/api/catalog-screen'}


def config_path(root):
    return Path(root) / 'config/jobs.json'


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError('jobs_invalid')
    known = {job['id'] for job in JOBS}
    supplied = raw.get('jobs', {})
    if supplied is None:
        supplied = {}
    # A list or any other shape is a mistake, not an empty schedule.
    if not isinstance(supplied, dict) or set(supplied) - known:
        raise ValueError('jobs_invalid')
    jobs = {}
    for job in JOBS:
        entry = supplied.get(job['id']) or {}
        if not isinstance(entry, dict):
            raise ValueError('jobs_invalid')
        allowed = {'enabled', 'at'} | ({'weekday'} if job.get('cadence') == 'weekly' else set())
        if set(entry) - allowed:
            raise ValueError('jobs_invalid')
        enabled = entry.get('enabled', False)
        if type(enabled) is not bool:
            raise ValueError('jobs_invalid')
        at = entry.get('at', job['defaultAt'])
        if at is not None:
            at = str(at)
            parts = at.split(':')
            if len(parts) != 2 or not all(part.isdigit() and len(part) == 2 for part in parts):
                raise ValueError('jobs_invalid')
            hour, minute = int(parts[0]), int(parts[1])
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError('jobs_invalid')
        setting = {'enabled': enabled, 'at': at}
        if job.get('cadence') == 'weekly':
            weekday = entry.get('weekday', job.get('defaultWeekday', 0))
            if type(weekday) is not int or not 0 <= weekday <= 6:
                raise ValueError('jobs_invalid')
            setting['weekday'] = weekday
        jobs[job['id']] = setting
    return {'version': DEFAULTS['version'], 'jobs': jobs}


def load(root):
    path = config_path(root)
    # An absent file means "everything off", which is the safe reading.
    raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(DEFAULTS)
    return validate(raw)


def save(root, raw):
    config = validate(raw)
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
    return config


def _scalar(db, sql, default=None):
    """Read one timestamp from a database that may not exist yet; never raise for a missing job."""
    path = Path(db)
    if not path.exists():
        return default
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            row = conn.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    except sqlite3.Error:
        return default


def last_run(root):
    """Last observed activity per job, only from sources that genuinely record it."""
    root = Path(root)
    return {'catalog_collect': _scalar(root / 'var/global-source.sqlite',
                                       'SELECT max(updated) FROM global_source_run'),
            'catalog_screen': _scalar(root / 'var/global-source.sqlite',
                                      'SELECT max(updated) FROM global_source_screen_run'),
            'link_prepare': _scalar(root / 'var/catalog-links.sqlite',
                                    'SELECT max(updated) FROM catalog_prepare_run'),
            'campaign_material_refresh': _maintenance_success(root, 'campaign'),
            'selected_taplink_verify': _maintenance_success(root, 'selected'),
            # 收信监控把"最近核验"写在它自己的状态文件里；那是真实记录，不是推算。
            'inbox_monitor': _status_stamp(root / 'var/cycle-inbox-status.json')}


def _status_stamp(path):
    """A monitor's own last-checked timestamp, or None. A torn file is simply no record."""
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    stamp = value.get('checkedAt')
    return float(stamp) if isinstance(stamp, (int, float)) and stamp > 0 else None


def _maintenance_success(root, key):
    try:
        value = json.loads((Path(root) / 'var/material-maintenance-status.json').read_text(encoding='utf-8'))
        stamp = (value.get('lastSuccess') or {}).get(key) if isinstance(value, dict) else None
        return float(stamp) if isinstance(stamp, (int, float)) and stamp > 0 else None
    except (OSError, ValueError):
        return None


def status(root=None):
    root = root or Path(__file__).resolve().parents[2]
    config = load(root)
    last = last_run(root)
    jobs = []
    for job in JOBS:
        setting = config['jobs'][job['id']]
        jobs.append({'id': job['id'], 'name': job['name'], 'group': job['group'],
                     'description': job['description'], 'manual': job['manual'],
                     'manualEndpoint': MANUAL_ENDPOINTS.get(job['manual']),
                     'lastRunAt': last.get(job['id']), 'enabled': setting['enabled'],
                     'schedulable': job['id'] in ('campaign_material_refresh','selected_taplink_verify'),
                     'at': setting['at'], 'cadence': job.get('cadence', 'daily'),
                     'weekday': setting.get('weekday')})
    from lib.material_maintenance import scheduler_state
    return {'version': config['version'], 'jobs': jobs,
            'schedulerReady': True, 'scheduler': scheduler_state(root)}
