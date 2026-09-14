"""Fixed-target task specification and durable full-preparation gate.

No platform/model calls. This ledger does not authorize or submit messages.
Delivery adapters must independently enforce live relationship, quota and receipts.
"""
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from lib.second_cycle import digest,encoded

class BatchError(ValueError):pass

def normalize_spec(value):
    if not isinstance(value,dict) or set(value)-{'institution','market','target','startTime','endTime','startDate','productScope','replyAfterSending','prepareNow','reserve'}:
        raise BatchError('invalid_task_spec')
    institution=value.get('institution');market=value.get('market');target=value.get('target')
    if not isinstance(institution,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}',institution):raise BatchError('institution_required')
    if market not in ('it','mx','br'):raise BatchError('market_required')
    if type(target) is not int or not 1<=target<=100000:raise BatchError('numeric_target_required')
    reserve=value.get('reserve',math.ceil(target/10))
    if type(reserve) is not int or not 0<=reserve<=target:raise BatchError('invalid_reserve')
    start=value.get('startTime');end=value.get('endTime')
    prepare=value.get('prepareNow',False)
    if type(prepare) is not bool:raise BatchError('invalid_prepare_mode')
    if prepare and any(value.get(k) is not None for k in ('startTime','endTime','startDate')):raise BatchError('unexpected_send_schedule')
    if not prepare and (any(not isinstance(t,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',t) for t in (start,end)) or start==end):raise BatchError('invalid_send_window')
    scope=value.get('productScope',{'kind':'all_eligible','values':[]})
    if not isinstance(scope,dict) or set(scope)!={'kind','values'} or scope['kind'] not in ('all_eligible','pids','categories','campaigns'):raise BatchError('invalid_product_scope')
    values=scope['values']
    if not isinstance(values,list) or len(values)>10000 or any(not isinstance(v,str) or not v.strip() or len(v)>200 for v in values):raise BatchError('invalid_product_scope')
    if (scope['kind']=='all_eligible')!= (len(values)==0):raise BatchError('invalid_product_scope')
    if scope['kind']=='pids' and any(not re.fullmatch(r'\d{19}',v) for v in values):raise BatchError('invalid_product_scope')
    start_date=value.get('startDate')
    if start_date is not None:
        try:
            if not isinstance(start_date,str) or date.fromisoformat(start_date).isoformat()!=start_date:raise ValueError()
        except (ValueError,TypeError):raise BatchError('invalid_start_date')
    reply=value.get('replyAfterSending',False)
    if type(reply) is not bool:raise BatchError('invalid_reply_policy')
    return {'institution':institution,'market':market,'target':target,'reserve':reserve,'timezone':'Asia/Shanghai',
            'startTime':start,'endTime':end,'startDate':start_date,'productScope':{'kind':scope['kind'],'values':sorted(set(values))},'replyAfterSending':reply,**({'prepareNow':True} if prepare else {})}

def in_window(spec,at):
    if not spec.get('startTime') or not spec.get('endTime'):return False
    local=datetime.fromtimestamp(at,ZoneInfo('Asia/Shanghai'));current=local.strftime('%H:%M')
    start,end=spec['startTime'],spec['endTime'];cross=start>end
    active=start<=current<end if not cross else current>=start or current<end
    anchor=local.date()-timedelta(days=1) if cross and current<end else local.date()
    return active and (not spec.get('startDate') or anchor>=date.fromisoformat(spec['startDate']))

def preparation_gate(target,members,*,partial_override=False,reserve=None):
    """Adapters supply verified stage evidence, not task-created placeholder success."""
    if type(target) is not int or target<=0:raise BatchError('numeric_target_required')
    reserve=math.ceil(target/10) if reserve is None else reserve
    if type(reserve) is not int or not 0<=reserve<=target:raise BatchError('invalid_reserve')
    seen=set();ready=[];issues={};materials=set()
    for row in members:
        oec=row.get('oec')
        if not isinstance(oec,str) or not re.fullmatch(r'[1-9][0-9]{0,39}',oec):raise BatchError('identity_missing')
        if oec in seen:raise BatchError('duplicate_creator')
        seen.add(oec)
        checks=row.get('checks',{})
        missing=[k for k in ('source','identity','relationship','offer','name','taplink') if checks.get(k) is not True]
        if not row.get('materialKey'):missing.append('material_binding')
        for k in missing:issues[k]=issues.get(k,0)+1
        if not missing:ready.append(oec);materials.add(row['materialKey'])
    required=target+reserve
    return {'required':required,'ready':len(ready),'missing':max(0,required-len(ready)),'issues':issues,
            'materialGroups':len(materials),'complete':len(ready)>=required,
            'mayEnterSendStage':len(ready)>=required or bool(partial_override and ready),
            'goalUnchanged':target,'readyOecs':ready}

SCHEMA='''
CREATE TABLE IF NOT EXISTS batch_task(id TEXT PRIMARY KEY,request_key TEXT UNIQUE NOT NULL,spec TEXT NOT NULL,spec_hash TEXT NOT NULL,state TEXT NOT NULL,priority INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,revision INTEGER NOT NULL DEFAULT 1,partial_override INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS batch_member(task_id TEXT NOT NULL,oec TEXT NOT NULL,material_key TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(task_id,oec));
CREATE TABLE IF NOT EXISTS batch_event(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,at REAL NOT NULL);
'''
class BatchTasks:
    def __init__(self,path,clock=time.time):
        self.clock=clock;self.db=sqlite3.connect(path,isolation_level=None,timeout=10);self.db.row_factory=sqlite3.Row;self.db.executescript(SCHEMA)
    def close(self):self.db.close()
    @contextmanager
    def tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:yield;self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise
    def get(self,id):
        row=self.db.execute('SELECT * FROM batch_task WHERE id=?',(id,)).fetchone()
        if not row:raise BatchError('task_missing')
        return dict(row)|{'spec':json.loads(row['spec'])}
    def event(self,id,kind,payload):self.db.execute('INSERT INTO batch_event(task_id,kind,payload,at) VALUES(?,?,?,?)',(id,kind,encoded(payload),self.clock()))
    def create(self,spec,request_key,confirmed_hash):
        value=normalize_spec(spec);fingerprint=digest(value)
        if fingerprint!=confirmed_hash:raise BatchError('task_confirmation_mismatch')
        if not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}',request_key):raise BatchError('invalid_request_key')
        with self.tx():
            prior=self.db.execute('SELECT id,spec_hash FROM batch_task WHERE request_key=?',(request_key,)).fetchone()
            if prior:
                if prior['spec_hash']!=fingerprint:raise BatchError('task_request_conflict')
                return self.get(prior['id'])
            id='batch-'+digest([request_key,value])[:28]
            self.db.execute("INSERT INTO batch_task(id,request_key,spec,spec_hash,state,created) VALUES(?,?,?,?,'preparing',?)",(id,request_key,encoded(value),fingerprint,self.clock()))
            self.event(id,'task_confirmed',{'specHash':fingerprint})
        return self.get(id)
    def record_member(self,id,member,*,expected_revision=None):
        with self.tx():
            task=self.get(id)
            if task['state']!='preparing':raise BatchError('task_not_preparing')
            spec=task['spec']
            if (member.get('institution'),member.get('market'))!=(spec['institution'],spec['market']):raise BatchError('member_scope_mismatch')
            scope=spec['productScope'];field={'pids':'pid','categories':'categoryId','campaigns':'campaignId'}.get(scope['kind'])
            if field and member.get(field) not in scope['values']:raise BatchError('member_outside_product_scope')
            preparation_gate(task['spec']['target'],[member],reserve=task['spec']['reserve'])
            previous=self.db.execute('SELECT payload FROM batch_member WHERE task_id=? AND oec=?',(id,member['oec'])).fetchone()
            if previous and previous[0]==encoded(member):return
            if previous and expected_revision!=task['revision']:raise BatchError('member_conflict')
            self.db.execute('INSERT INTO batch_member VALUES(?,?,?,?) ON CONFLICT(task_id,oec) DO UPDATE SET material_key=excluded.material_key,payload=excluded.payload',(id,member['oec'],member.get('materialKey',''),encoded(member)))
            self.db.execute('UPDATE batch_task SET revision=revision+1 WHERE id=?',(id,))
            self.event(id,'member_prepared' if not previous else 'member_preparation_updated',{'oec':member['oec'],'previousHash':digest(json.loads(previous[0])) if previous else None,'currentHash':digest(member)})
    def readiness(self,id):
        task=self.get(id);rows=[json.loads(r[0]) for r in self.db.execute('SELECT payload FROM batch_member WHERE task_id=?',(id,))]
        return preparation_gate(task['spec']['target'],rows,partial_override=bool(task['partial_override']),reserve=task['spec']['reserve'])
    def freeze(self,id):
        with self.tx():
            task=self.get(id)
            if task['state']!='preparing':raise BatchError('task_not_preparing')
            gate=self.readiness(id)
            if not gate['complete']:raise BatchError('full_preparation_required')
            self.db.execute("UPDATE batch_task SET state='ready',revision=revision+1 WHERE id=?",(id,));self.event(id,'preparation_frozen',{'ready':gate['ready'],'materials':gate['materialGroups']})
    def allow_partial(self,id,*,expected_revision,confirmed):
        with self.tx():
            task=self.get(id)
            if confirmed is not True or task['revision']!=expected_revision or task['state']!='preparing':raise BatchError('partial_confirmation_required')
            if self.readiness(id)['ready']<1:raise BatchError('no_ready_members')
            self.db.execute("UPDATE batch_task SET partial_override=1,state='ready',revision=revision+1 WHERE id=?",(id,));self.event(id,'partial_explicitly_confirmed',{'target':task['spec']['target']})
    def can_start(self,id,at=None):
        task=self.get(id);spec=task['spec'];reasons=[]
        if task['state']!='ready':reasons.append('task_not_ready')
        if not in_window(spec,self.clock() if at is None else at):reasons.append('outside_send_window')
        if not self.readiness(id)['mayEnterSendStage']:reasons.append('preparation_incomplete')
        for r in self.db.execute("SELECT * FROM batch_task WHERE id<>? AND state NOT IN ('completed','cancelled','paused')",(id,)):
            other=json.loads(r['spec'])
            if (other['institution'],other['market'])==(spec['institution'],spec['market']) and (-r['priority'],r['created'],r['id'])<(-task['priority'],task['created'],id):reasons.append('earlier_task_in_scope');break
        return {'stageEligible':not reasons,'reasons':reasons,'platformDispatchAuthorized':False}

def reply_stage_enabled(*,task_allows,manual_pause,scope_phase):
    return task_allows is True and manual_pause is False and scope_phase=='replying'
