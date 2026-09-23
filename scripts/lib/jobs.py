"""Real operating jobs and their persisted, default-off schedule intent."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path


DEFAULTS = {'version': 'jobs-v3', 'jobs': {}}
JOBS = (
    {'id':'taplink_clean','name':'TapLink 清洗','group':'材料','description':'周一扫描平台卡；当前仅 IT 会删除平台明确失效且已回查的卡。','manual':'workflow','defaultAt':'04:30','cadence':'weekly','defaultWeekday':0},
    {'id':'full_catalog_update','name':'全托周更新','group':'货盘','description':'每周普通刷新；新市场首次与每 30 天按一级类目完整刷新。','manual':'workflow','defaultAt':'04:40','cadence':'weekly','defaultWeekday':0},
    {'id':'campaign_catalog_update','name':'Campaign 每两天更新','group':'货盘','description':'每 2 天 07:00 完整刷新 Campaign；每次重新判断失效、库存、佣金与期限。','manual':'workflow','defaultAt':'07:00','cadence':'daily'},
    {'id':'taplink_prepare','name':'TapLink 准备','group':'材料','description':'货盘发布后核验并创建缺失标准链接；unknown 只回查原意图。','manual':'workflow','defaultAt':'07:20','cadence':'daily'},
    {'id':'kalodata_leads','name':'Kalodata','group':'达人','description':'整批读取 A/B 线索；最多两个市场并行，范围完成或真实额度耗尽后发布。','manual':'workflow','defaultAt':'08:00','cadence':'daily'},
    {'id':'oecid','name':'OECID','group':'达人','description':'集中处理本轮新增且尚未判定的 handle，cohort 间释放通信账号。','manual':'workflow','defaultAt':'09:00','cadence':'daily'},
    {'id':'send_pool_publish','name':'发送池发布','group':'发送','description':'OECID 阶段结束后统一重算并发布当前发送池。','manual':'workflow','defaultAt':'10:00','cadence':'daily'},
    {'id':'inbox_monitor','name':'收信监控','group':'会话','description':'全天只读收信并从 checkpoint 补捞；不授予回复发送。','manual':'job-control','defaultAt':'00:00','cadence':'daily'},
    {'id':'agent_reply','name':'Agent 回复','group':'会话','description':'北京时间 15:00–16:00 集中处理；开关与模板在会话页。','manual':'job-control','defaultAt':'15:00','cadence':'daily'},
    {'id':'continuous_send','name':'持续二发','group':'发送','description':'北京时间 16:30–24:00 持续消费发送池，次日从台账继续。','manual':'job-control','defaultAt':'16:30','cadence':'daily'},
)
MANUAL_ENDPOINTS = {'workflow':'/api/workflow','job-control':'/api/jobs'}


def config_path(root):
    return Path(root) / 'config/jobs.json'


def validate(raw):
    if not isinstance(raw,dict):raise ValueError('jobs_invalid')
    known={job['id'] for job in JOBS};supplied=raw.get('jobs',{})
    if supplied is None:supplied={}
    if not isinstance(supplied,dict) or set(supplied)-known:raise ValueError('jobs_invalid')
    jobs={}
    for job in JOBS:
        entry=supplied.get(job['id']) or {}
        if not isinstance(entry,dict):raise ValueError('jobs_invalid')
        allowed={'enabled','at'}|({'weekday'} if job.get('cadence')=='weekly' else set())
        if set(entry)-allowed:raise ValueError('jobs_invalid')
        enabled=entry.get('enabled',False);at=entry.get('at',job['defaultAt'])
        if type(enabled) is not bool:raise ValueError('jobs_invalid')
        if at is not None:
            parts=str(at).split(':')
            if len(parts)!=2 or not all(part.isdigit() and len(part)==2 for part in parts):raise ValueError('jobs_invalid')
            hour,minute=map(int,parts)
            if not 0<=hour<=23 or not 0<=minute<=59:raise ValueError('jobs_invalid')
            at=str(at)
        setting={'enabled':enabled,'at':at}
        if job.get('cadence')=='weekly':
            weekday=entry.get('weekday',job.get('defaultWeekday',0))
            if type(weekday) is not int or not 0<=weekday<=6:raise ValueError('jobs_invalid')
            setting['weekday']=weekday
        jobs[job['id']]=setting
    return {'version':DEFAULTS['version'],'jobs':jobs}


def load(root):
    path=config_path(root);raw=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(DEFAULTS)
    return validate(raw)


def save(root,raw):
    config=validate(raw);path=config_path(root);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.json.tmp');temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temporary.replace(path)
    return config


def _scalar(path,sql,args=()):
    path=Path(path)
    if not path.exists():return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute(sql,args).fetchone()
        return row[0] if row and row[0] is not None else None
    except sqlite3.Error:return None


def _workflow_stage(root,stage,market):
    return _scalar(Path(root)/'var/second-cycle.sqlite',
      "SELECT max(s.finished_at) FROM workflow_stage_run s JOIN workflow_run r ON r.run_id=s.run_id WHERE r.market=? AND s.stage=? AND s.state IN ('completed','quota_exhausted')",(market,stage))


def _status_stamp(path):
    try:value=json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return None
    stamp=value.get('checkedAt') if isinstance(value,dict) else None
    return float(stamp) if isinstance(stamp,(int,float)) and stamp>0 else None


def last_run(root,market):
    root=Path(root);cycle=root/'var/second-cycle.sqlite'
    return {'taplink_clean':_workflow_stage(root,'taplink_clean',market),
      'full_catalog_update':_workflow_stage(root,'catalog',market),
      'campaign_catalog_update':_workflow_stage(root,'catalog',market),
      'taplink_prepare':_workflow_stage(root,'taplink_prepare',market),
      'kalodata_leads':_workflow_stage(root,'kalodata',market),'oecid':_workflow_stage(root,'oecid',market),
      'send_pool_publish':_workflow_stage(root,'send_pool',market),
      'inbox_monitor':_status_stamp(root/'var/cycle-inbox-status.json') if market=='it' else None,
      'agent_reply':_scalar(cycle,"SELECT max(a.finished_at) FROM agent_reply_run a JOIN plan p ON p.id=a.plan_id WHERE p.market=?",(market,)),
      'continuous_send':_scalar(cycle,"SELECT max(c.last_success_at) FROM continuous_send_runtime c JOIN plan p ON p.id=c.plan_id WHERE p.market=?",(market,))}


def status(root=None,market='it'):
    root=Path(root or Path(__file__).resolve().parents[2]);config=load(root);last=last_run(root,market);jobs=[]
    for job in JOBS:
        setting=config['jobs'][job['id']]
        jobs.append({'id':job['id'],'name':job['name'],'group':job['group'],'description':job['description'],
          'manual':job['manual'],'manualEndpoint':MANUAL_ENDPOINTS[job['manual']],
          'lastRunAt':last.get(job['id']),'enabled':setting['enabled'],'schedulable':True,
          'at':setting['at'],'cadence':job.get('cadence','daily'),'weekday':setting.get('weekday')})
    from lib.operations_scheduler import scheduler_state
    return {'version':config['version'],'market':market,'jobs':jobs,'schedulerReady':True,'scheduler':scheduler_state(root)}
