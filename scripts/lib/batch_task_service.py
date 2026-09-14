"""Confirmed task cards and restartable local preparation reconciliation.

The local reader records reusable evidence; optional scoped preparation adapters
handle HTTP, identity and materials. Dispatch authority remains disabled.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from lib.batch_tasks import BatchTasks, BatchError, normalize_spec, preparation_gate
from lib.second_cycle import CycleStore, digest, encoded, assess_offer
from lib.cycle_materials import name_key, TEMPLATES

POLICY = {
    'version': 'second-batch-20260914', 'templateVersion': 4,
    'authorizationScope': 'full_preparation_no_messages',
    'templates': TEMPLATES, 'fullManagedStockRequired': False,
    'allowedPreparationActions':['kalodata_read','identity_read','product_names','card_read','card_create_for_selected_or_campaign'],
    'sourceWindowDays':14, 'sourceLagDays':2, 'sourceMaxPagesPerPid':2,
    'ordinaryStockMinimumExclusive': 100, 'campaignDaysMinimumExclusive': 45,
    'preparation': 'whole_target_plus_reserve', 'counting': 'unique_oec_card_and_text_confirmed',
    'manualReplyPauseOverridesTask': True,
}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS batch_confirmation(token TEXT PRIMARY KEY,spec TEXT NOT NULL,policy TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS batch_policy(task_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batch_preparation_run(task_id TEXT PRIMARY KEY,report TEXT,checked REAL,next_run REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS batch_worker(singleton INTEGER PRIMARY KEY CHECK(singleton=1),heartbeat REAL NOT NULL);
'''

def in_scope(spec, offer):
    scope=spec['productScope'];field={'pids':'pid','categories':'categoryId','campaigns':'campaignId'}.get(scope['kind'])
    return not field or str(offer.get(field,'')) in scope['values']

class TaskService:
    def __init__(self,path,clock=None):
        self.tasks=BatchTasks(path,**({'clock':clock} if clock else {}));self.db=self.tasks.db
        self.db.executescript(SCHEMA)
    def close(self):self.tasks.close()
    def preview(self,spec):
        value=normalize_spec(spec)
        if not value['startDate'] and not value.get('prepareNow'):raise BatchError('start_date_required')
        # Other markets remain visible in the product, but this preparation adapter
        # has only Italy evidence and Italian material support.
        if (value['institution'],value['market'])!=('bjn-local-research','it'):raise BatchError('scope_adapter_unavailable')
        token=digest([value,POLICY])
        with self.tasks.tx():self.db.execute('INSERT OR IGNORE INTO batch_confirmation VALUES(?,?,?,?)',(token,encoded(value),encoded(POLICY),self.tasks.clock()))
        return {'token':token,'spec':value,'policy':POLICY,'executionConnected':False}
    def confirm(self,token,request_key):
        row=self.db.execute('SELECT * FROM batch_confirmation WHERE token=?',(token,)).fetchone()
        if not row:raise BatchError('task_card_missing')
        value=json.loads(row['spec']);policy=json.loads(row['policy'])
        if policy!=POLICY:raise BatchError('task_card_stale')
        # Everything, including the durable preparation outbox, commits together.
        with self.tasks.tx():
            prior=self.db.execute('SELECT id,spec_hash FROM batch_task WHERE request_key=?',(request_key,)).fetchone()
            if prior:
                if prior['spec_hash']!=digest(value):raise BatchError('task_request_conflict')
                return self.detail(prior['id'])
            import re
            if not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}',request_key):raise BatchError('invalid_request_key')
            id='batch-'+digest([request_key,value])[:28]
            self.db.execute("INSERT INTO batch_task(id,request_key,spec,spec_hash,state,created) VALUES(?,?,?,?,'preparing',?)",(id,request_key,encoded(value),digest(value),self.tasks.clock()))
            self.db.execute('INSERT INTO batch_policy VALUES(?,?)',(id,encoded(policy)))
            self.db.execute('INSERT INTO batch_preparation_run(task_id) VALUES(?)',(id,))
            self.tasks.event(id,'task_confirmed',{'cardToken':token,'policyHash':digest(policy)})
        return self.detail(id)
    def detail(self,id):
        t=self.tasks.get(id);r=self.db.execute('SELECT * FROM batch_preparation_run WHERE task_id=?',(id,)).fetchone()
        policy=self.db.execute('SELECT payload FROM batch_policy WHERE task_id=?',(id,)).fetchone()
        return {k:t[k] for k in ('id','spec','state','priority','revision','created')}|{
            'preparation':json.loads(r['report']) if r and r['report'] else None,
            'sourcePreparation':self.source_status(id),
            'preparationSpeed':self.preparation_speed(id),
            'stabilityTest':self.stability_status(id),
            'releaseValidation':self.release_status(id),
            'checkedAt':r['checked'] if r else None,'nextCheckAt':r['next_run'] if r else None,
            'policy':json.loads(policy[0]) if policy else None,'executionConnected':False,
            'events':[dict(e)|{'payload':json.loads(e['payload'])} for e in self.db.execute('SELECT kind,payload,at FROM batch_event WHERE task_id=? ORDER BY id DESC LIMIT 12',(id,))]}
    def release_status(self,id):
        from lib.identity_acceptance import acceptance_status
        return acceptance_status(self,id)
    def stability_status(self,id):
        from lib.identity_soak import status
        return status(self,id)
    def preparation_speed(self,id):
        end=self.db.execute("SELECT at,payload FROM batch_event WHERE task_id=? AND kind='preparation_observed' AND json_extract(payload,'$.candidates') IS NOT NULL ORDER BY id DESC LIMIT 1",(id,)).fetchone()
        if not end:return None
        start=self.db.execute("SELECT at,payload FROM batch_event WHERE task_id=? AND kind='preparation_observed' AND json_extract(payload,'$.candidates') IS NOT NULL AND at<=? ORDER BY id DESC LIMIT 1",(id,self.tasks.clock()-300)).fetchone()
        if not start:start=self.db.execute("SELECT at,payload FROM batch_event WHERE task_id=? AND kind='preparation_observed' AND json_extract(payload,'$.candidates') IS NOT NULL ORDER BY id LIMIT 1",(id,)).fetchone()
        seconds=self.tasks.clock()-max(start['at'],self.tasks.clock()-300);delta=json.loads(end['payload'])['candidates']-json.loads(start['payload'])['candidates']
        return {'windowSeconds':round(seconds,1),'netCandidates':delta,'perMinute':round(delta*60/seconds,2) if seconds>0 else 0,'observedAt':self.tasks.clock()}
    def source_status(self,id):
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='batch_source_job'").fetchone():return None
        from lib.batch_sources import BatchSources
        return BatchSources(self,initialize=False).status(id)
    def listing(self):
        rows=self.db.execute('SELECT id FROM batch_task ORDER BY priority DESC,created,id LIMIT 100').fetchall()
        h=self.db.execute('SELECT heartbeat FROM batch_worker WHERE singleton=1').fetchone()
        return {'tasks':[self.detail(r[0]) for r in rows], 'worker':{'heartbeat':h[0] if h else None,'running':bool(h and self.tasks.clock()-h[0]<45)},'executionConnected':False}
    def control(self,id,action,revision,priority=None):
        with self.tasks.tx():
            t=self.tasks.get(id)
            if type(revision) is not int or revision!=t['revision']:raise BatchError('revision_conflict')
            if action=='pause' and t['state'] in ('preparing','ready'):state='paused'
            elif action=='resume' and t['state']=='paused':state='preparing'
            elif action=='priority' and t['state'] in ('preparing','paused') and type(priority) is int and -100<=priority<=100:state=t['state']
            else:raise BatchError('invalid_task_control')
            self.db.execute('UPDATE batch_task SET state=?,priority=?,revision=revision+1 WHERE id=?',(state,priority if action=='priority' else t['priority'],id))
            self.db.execute('UPDATE batch_preparation_run SET next_run=0 WHERE task_id=?',(id,))
            if action=='resume' and self.db.execute("SELECT 1 FROM sqlite_master WHERE name='batch_source_job'").fetchone():
                self.db.execute("UPDATE batch_source_job SET state='queued',error=NULL,next_attempt=0 WHERE task_id=? AND state='blocked'",(id,))
            self.tasks.event(id,action,{'priority':priority} if action=='priority' else {})
        return self.detail(id)
    def heartbeat(self):
        self.db.execute('INSERT INTO batch_worker VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET heartbeat=excluded.heartbeat',(self.tasks.clock(),))
    def tick(self,read,prepare=None):
        self.heartbeat()
        row=self.db.execute("SELECT t.id FROM batch_task t JOIN batch_preparation_run r ON r.task_id=t.id WHERE t.state='preparing' AND r.next_run<=? ORDER BY t.priority DESC,t.created,t.id LIMIT 1",(self.tasks.clock(),)).fetchone()
        if not row:return False
        t=self.tasks.get(row[0])
        try:
            if prepare:prepare(t)
            if self.tasks.get(t['id'])['state']!='preparing':return True
            report,members=read(t['spec'])
            from lib.batch_materials import apply_materials
            report,members=apply_materials(self,t['id'],report,members)
            preparation_gate(t['spec']['target'],members,reserve=t['spec']['reserve'])
            if len(members)>t['spec']['target']+t['spec']['reserve'] or any((m.get('institution'),m.get('market'))!=(t['spec']['institution'],t['spec']['market']) or not in_scope(t['spec'],m) for m in members):raise BatchError('member_scope_mismatch')
            soak_status=self.stability_status(t['id'])
            if soak_status and soak_status.get('sourceQuotaExhausted'):report['blockers'].append('kalodata_daily_quota_exhausted')
            if soak_status and soak_status['state']=='attention':report['blockers'].append('identity_soak_attention')
            report['membershipHash']=digest(members)
        except Exception:
            report={'state':'waiting_data','blockers':['local_source_unavailable'],'executionAllowed':False};members=None
        with self.tasks.tx():
            current=self.tasks.get(t['id'])
            if current['revision']!=t['revision'] or current['state']!='preparing':return True
            previous=self.db.execute('SELECT report FROM batch_preparation_run WHERE task_id=?',(t['id'],)).fetchone()[0]
            if members is not None and previous!=encoded(report):
                # Only provisional preparation observations, never delivery records.
                self.db.execute('DELETE FROM batch_member WHERE task_id=?',(t['id'],))
                for m in members:
                    self.db.execute('INSERT INTO batch_member VALUES(?,?,?,?)',(t['id'],m['oec'],m['materialKey'],encoded(m)))
            delay=30
            if report.get('candidateGap')==0 and report.get('materialJobs',{}).get('pending') and not any('unresolved' in b or 'changed' in b for b in report.get('blockers',[])):delay=2
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='batch_source_job'").fetchone() and self.db.execute("SELECT 1 FROM batch_source_job WHERE task_id=? AND state='queued' AND next_attempt<=?",(t['id'],self.tasks.clock())).fetchone() and not self.db.execute("SELECT 1 FROM batch_source_job WHERE task_id=? AND state='blocked'",(t['id'],)).fetchone():delay=2
            self.db.execute('UPDATE batch_preparation_run SET report=?,checked=?,next_run=? WHERE task_id=?',(encoded(report),self.tasks.clock(),self.tasks.clock()+delay,t['id']))
            if previous!=encoded(report):
                self.db.execute('UPDATE batch_task SET revision=revision+1 WHERE id=?',(t['id'],))
                self.tasks.event(t['id'],'preparation_observed',{'state':report['state'],'candidates':report.get('candidates',0),'blockers':report['blockers']})
        if members is not None and self.db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_soak_run'").fetchone():
            from lib.identity_soak import IdentitySoak
            soak=IdentitySoak(self)
            for row in self.db.execute("SELECT id FROM identity_soak_run WHERE task_id=? AND state='active'",(t['id'],)).fetchall():soak.checkpoint(row[0],members)
        if report.get('state')=='ready' and self.tasks.get(t['id'])['state']=='preparing':self.tasks.freeze(t['id'])
        return True


def read_local_preparation(root,spec):
    """Whole task, no daily quota cap; exact per-edge consumption and scope checks."""
    root=Path(root)
    with CycleStore(root/'var/second-cycle.sqlite',readonly=True) as store,closing(sqlite3.connect((root/'var/creator-identities.sqlite').as_uri()+'?mode=ro',uri=True)) as ids:
        store.db.execute('BEGIN');ids.execute('BEGIN')
        plan=store.db.execute('SELECT id FROM plan WHERE institution=? AND market=?',(spec['institution'],spec['market'])).fetchone()
        if not plan:raise BatchError('scope_missing')
        p=plan[0];eligible=store._eligible_people(p)
        offers={}
        for _,o in store._offers(p):
            if in_scope(spec,o) and assess_offer(o,store.clock())['eligible']:offers.setdefault(o['pid'],[]).append(o)
        names={r['id']:json.loads(r['payload']) for r in store.db.execute('SELECT id,payload FROM cycle_product_name')}
        cards={(r['offer_key'],r['offer_fingerprint']):json.loads(r['payload']) for r in store.db.execute('SELECT * FROM cycle_card_check WHERE plan_id=?',(p,))}
        consumed={(r[0],r[1],r[2]) for r in store.db.execute("SELECT creator_id,pid,source_id FROM cycle_delivery WHERE plan_id=? AND state IN ('ready','running','unknown','confirmed','partial_delivery')",(p,))}
        active={r[0] for r in store.db.execute("SELECT creator_id FROM cycle_delivery WHERE plan_id=? AND state IN ('ready','running','unknown','partial_delivery')",(p,))}
        identities={(r[0],r[1]) for r in ids.execute('SELECT creator_id,oec_id FROM creator_identity WHERE market=? AND handle_conflict=0',(spec['market'],))}
        base_oecs=set();soak_allowed=None
        task_path=root/'var/batch-tasks.sqlite'
        if task_path.exists():
            with closing(sqlite3.connect(task_path.as_uri()+'?mode=ro',uri=True)) as control:
                if control.execute("SELECT 1 FROM sqlite_master WHERE name='identity_soak_run'").fetchone():
                    run=control.execute("SELECT s.id FROM identity_soak_run s JOIN batch_task t ON t.id=s.task_id WHERE s.state IN ('active','attention') AND json_extract(t.spec,'$.institution')=? AND json_extract(t.spec,'$.market')=? ORDER BY s.started DESC LIMIT 1",(spec['institution'],spec['market'])).fetchone()
                    if run:
                        base_oecs={r[0] for r in control.execute("SELECT oec FROM identity_soak_member WHERE run_id=? AND role='baseline'",(run[0],))}
                        soak_allowed=base_oecs|{r[0] for r in control.execute('SELECT oec FROM identity_soak_proof WHERE run_id=?',(run[0],))}
        people={}
        for row in store.db.execute('SELECT r.creator_id,r.oec,e.source_id,e.payload FROM cycle_identity_resolution r JOIN source_edge e USING(plan_id,source_id) WHERE r.plan_id=?',(p,)):
            edge=json.loads(row['payload']);creator=row['creator_id']
            if soak_allowed is not None and row['oec'] not in soak_allowed:continue
            if creator not in eligible or creator in active or (creator,row['oec']) not in identities:continue
            if edge.get('sourceKind')!='kalodata_http' or (creator,edge['pid'],row['source_id']) in consumed:continue
            for o in offers.get(edge['pid'],[]):
                name=names.get(name_key(o));card=cards.get((o['offerKey'],digest(o)))
                located=bool(card and card.get('state')=='verified_read_only' and card.get('pid')==o['pid'] and card.get('sourceCampaignId')==o.get('campaignId') and card.get('creatorPercent')==o['creatorPercent'] and card.get('listId') and card.get('evidenceRefs'))
                key=digest([spec['institution'],spec['market'],o,name_key(o),POLICY])
                m={'oec':row['oec'],'institution':spec['institution'],'market':spec['market'],'pid':o['pid'],'campaignId':o.get('campaignId'),'categoryId':o.get('categoryId'),'materialKey':key,'offerKey':o['offerKey'],'offerFingerprint':digest(o),'sourceId':row['source_id'],'sourceEvidenceRef':edge.get('evidenceRef'),'nameKey':name_key(o),'shortName':name.get('shortNameZh') if name else None,'cardObservation':card if located else None,
                   'checks':dict(source=True,identity=True,relationship=True,offer=True,name=bool(name),taplink=False),'cardLocated':located,'livePreparationVerified':False}
                rank=(int(bool(name))+int(located),edge.get('windowEnd',''),edge.get('units',0),o['offerKey'])
                if row['oec'] not in people or rank>people[row['oec']][0]:people[row['oec']]=(rank,m)
        required=spec['target']+spec['reserve'];selected=[x[1] for x in sorted(people.values(),key=lambda x:(x[1]['oec'] in base_oecs,x[0],x[1]['oec']),reverse=True)[:required]]
        groups={m['materialKey']:m for m in selected}
        for i,m in enumerate(selected):m['role']='formal' if i<spec['target'] else 'reserve'
        named=sum(m['checks']['name'] for m in selected);located=sum(m['cardLocated'] for m in selected)
        gap=max(0,required-len(selected));name_groups=sum(not m['checks']['name'] for m in groups.values())
        blockers=[]
        if gap:blockers.append('pid_creator_supply_needed')
        if name_groups:blockers.append('product_names_needed')
        blockers.append('task_taplink_adapter_pending')
        report={'state':'waiting_adapters','target':spec['target'],'reserve':spec['reserve'],'required':required,'candidates':len(selected),'availableCandidates':len(people),'candidateGap':gap,'formal':min(spec['target'],len(selected)),'reserves':max(0,len(selected)-spec['target']),'named':named,'cardsLocated':located,'verifiedReady':0,'materialGroups':len(groups),'namesNeeded':name_groups,'eligiblePids':len(offers),'blockers':blockers,'executionAllowed':False,
                'stages':[{'key':'source','label':'PID 与达人线索','done':len(selected),'required':required},{'key':'identity','label':'身份与关系本地校验','done':len(selected),'required':required},{'key':'name','label':'商品短名','done':named,'required':required},{'key':'taplink','label':'TapLink 任务核验','done':0,'required':required}],
                'products':[{'pid':m['pid'],'shortName':m['shortName'],'cardLocated':m['cardLocated'],'nameReady':m['checks']['name']} for m in list(groups.values())[:20]]}
        return report,selected
