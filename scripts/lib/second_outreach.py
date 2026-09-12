"""Italy second-outreach facts and immutable draft packets, independent of rehearsal."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'apps/agent/skills/it-intent-invitation/v1.md'


class SecondOutreachError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,159}', value):
        raise SecondOutreachError('invalid_request')
    return value


def default_source():
    from lib.second_outreach_source import load_second_source
    return load_second_source(root=ROOT)


class SecondOutreachStore:
    def __init__(self, var_dir=None, *, source=None, clock=now, promotion_facts=None):
        self.var = Path(var_dir or ROOT / 'var')
        self.var.mkdir(parents=True, exist_ok=True)
        self.source, self.clock = source or default_source, clock
        self.promotion_facts=promotion_facts or self._promotion_facts
        self.db = sqlite3.connect(self.var / 'second-outreach.sqlite', isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS second_meta(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,source_fingerprint TEXT,updated_at TEXT);
        INSERT OR IGNORE INTO second_meta VALUES(1,1,NULL,NULL);
        CREATE TABLE IF NOT EXISTS second_opportunity(id TEXT PRIMARY KEY,payload TEXT NOT NULL,fingerprint TEXT NOT NULL,revision INTEGER NOT NULL,control TEXT NOT NULL,active INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS second_packet(id TEXT PRIMARY KEY,opportunity_id TEXT NOT NULL,source_fingerprint TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS second_packet_opportunity ON second_packet(opportunity_id,created_at);
        CREATE TABLE IF NOT EXISTS second_command(id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS second_template_draft(id TEXT PRIMARY KEY,opportunity_id TEXT NOT NULL,revision INTEGER NOT NULL,source_fingerprint TEXT NOT NULL,context_fingerprint TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS second_template_selection(opportunity_id TEXT PRIMARY KEY,draft_id TEXT NOT NULL);
        ''')
        if self.db.execute('SELECT version FROM second_meta WHERE id=1').fetchone()[0] != 1:
            self.close()
            raise SecondOutreachError('unsupported_schema', 503)

    def close(self):self.db.close()
    def __enter__(self):return self
    def __exit__(self, *_):self.close()

    def _promotion_facts(self):
        path=self.var/'second-promotion-facts.json'
        try:
            if path.stat().st_size>131072:return {}
            value=json.loads(path.read_text())
            return value['products'] if value.get('schema')=='bdhub.second-promotion-facts.v1' and isinstance(value.get('products'),dict) else {}
        except (OSError,ValueError,TypeError):return {}

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def sync(self):
        with self.transaction():
            # Read the source only after acquiring the projection writer lock.
            # Otherwise an older reader can commit after a newer observation.
            bundle = self.source()
            rows = bundle.get('opportunities')
            if not isinstance(rows, list) or not rows or len({r['id'] for r in rows}) != len(rows):
                raise SecondOutreachError('source_invalid', 422)
            meta = self.db.execute('SELECT * FROM second_meta WHERE id=1').fetchone()
            if meta['source_fingerprint'] == bundle['sourceFingerprint']:
                return self.status()
            ids = {r['id'] for r in rows}
            for row in rows:
                previous = self.db.execute('SELECT * FROM second_opportunity WHERE id=?', (row['id'],)).fetchone()
                changed = previous is None or previous['fingerprint'] != row['fingerprint'] or not previous['active']
                revision = (previous['revision'] + int(changed)) if previous else 1
                self.db.execute('''INSERT INTO second_opportunity VALUES(?,?,?,?,?,1)
                    ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,fingerprint=excluded.fingerprint,revision=excluded.revision,active=1''',
                    (row['id'], canonical(row), row['fingerprint'], revision, previous['control'] if previous else 'active'))
            # A removed source is retained for audit, never silently left actionable.
            for row in self.db.execute('SELECT id FROM second_opportunity WHERE active=1').fetchall():
                if row['id'] not in ids:
                    self.db.execute('UPDATE second_opportunity SET active=0,revision=revision+1 WHERE id=?', (row['id'],))
            self.db.execute('UPDATE second_meta SET source_fingerprint=?,updated_at=? WHERE id=1', (bundle['sourceFingerprint'], self.clock()))
        return self.status()

    def transport_status(self):
        # A read-only auth probe is evidence of authentication, never permission to send.
        path = self.var / 'italy-im-auth-latest.json'
        base = {'state':'not_tested','reason':None,'observedAt':None,'sendEnabled':False}
        try:
            data = json.loads(path.read_text())
            if data.get('schema') != 'bdhub.italy-im-auth.v1' or data.get('market') != 'it':return base
            return {**base,'state':'auth_verified' if data.get('status') == 'auth_verified' else 'blocked',
                    'reason':data.get('reason') if isinstance(data.get('reason'), str) and re.fullmatch(r'[a-z0-9_]{1,100}',data['reason']) else None,
                    'observedAt':data.get('finishedAt') if isinstance(data.get('finishedAt'), str) else None}
        except (OSError,ValueError,TypeError):return base

    def status(self):
        rows = [self._public(r) for r in self.db.execute('SELECT * FROM second_opportunity WHERE active=1')]
        meta = self.db.execute('SELECT * FROM second_meta WHERE id=1').fetchone()
        identified = sum(r['currentRecipient'] is not None for r in rows)
        return {'market':'it','opportunities':len(rows),'identified':identified,'unresolved':len(rows)-identified,
                'products':len({p['pid'] for r in rows for p in r['products']}),'edges':sum(len(r['products']) for r in rows),
                'paused':sum(r['control']=='paused' for r in rows),'sourceFingerprint':meta['source_fingerprint'],'updatedAt':meta['updated_at'],
                'realSends':0,'resultUnknown':0,'transport':self.transport_status()}

    def _public(self, row):
        data = json.loads(row['payload'])
        packet = self.db.execute('SELECT id FROM second_packet WHERE opportunity_id=? AND revision=? AND source_fingerprint=? ORDER BY created_at,id LIMIT 1', (row['id'],row['revision'],row['fingerprint'])).fetchone()
        draft=self.db.execute('SELECT d.payload FROM second_template_selection s JOIN second_template_draft d ON s.draft_id=d.id WHERE s.opportunity_id=? AND d.revision=? AND d.source_fingerprint=?',(row['id'],row['revision'],row['fingerprint'])).fetchone()
        saved=json.loads(draft[0]) if draft else None
        if saved:
            from lib.second_templates import list_templates
            active=next((t for t in list_templates() if t['id']==saved['templateId']),None)
            if not active or active['fingerprint']!=saved['templateFingerprint']:saved=None
        return {**data,'revision':row['revision'],'control':row['control'],'packetId':packet[0] if packet else None,'templateDraft':saved}

    def get(self, opportunity_id):
        row = self.db.execute('SELECT * FROM second_opportunity WHERE id=? AND active=1', (token(opportunity_id),)).fetchone()
        if not row:raise SecondOutreachError('opportunity_not_found',404)
        return self._public(row)

    def list(self, *, offset=0, limit=20, q='', filter='all'):
        if type(offset) is not int or not 0<=offset<=100000 or type(limit) is not int or not 1<=limit<=100 or not isinstance(q,str) or len(q)>100 or filter not in ('all','identified','unresolved'):
            raise SecondOutreachError('invalid_request')
        rows=[self._public(r) for r in self.db.execute('SELECT * FROM second_opportunity WHERE active=1 ORDER BY id')]
        rows=[r for r in rows if (not q or q.lower().lstrip('@') in ' '.join([r['sourceHandle'], (r['currentRecipient'] or {}).get('handle') or '', *[p['title'] for p in r['products']]]).lower())
              and (filter=='all' or (r['currentRecipient'] is not None)==(filter=='identified'))]
        rows.sort(key=lambda r:(r['currentRecipient'] is None,-sum(p['units'] for p in r['products']),r['id']))
        return {'items':rows[offset:offset+limit],'total':len(rows),'offset':offset,'limit':limit}

    def command(self, request_id, command):
        token(request_id)
        if not isinstance(command,dict):raise SecondOutreachError('invalid_request')
        kind=command.get('type')
        fields={'refresh':{'type'},'prepare':{'type','opportunityId','expectedRevision'},'render_template':{'type','opportunityId','expectedRevision','templateId'},'render_batch':{'type','opportunityIds','templateId','expectedSourceFingerprint'},'control':{'type','opportunityId','expectedRevision','control'}}
        if not isinstance(kind,str) or kind not in fields or set(command)!=fields[kind]:raise SecondOutreachError('invalid_request')
        if kind=='render_batch':
            ids=command['opportunityIds']
            if not isinstance(ids,list) or not 1<=len(ids)<=100 or not isinstance(command['expectedSourceFingerprint'],str) or not re.fullmatch(r'[0-9a-f]{64}',command['expectedSourceFingerprint']):raise SecondOutreachError('invalid_request')
            command={**command,'opportunityIds':list(dict.fromkeys(token(value) for value in ids)),'templateId':token(command['templateId'])}
        request_hash=fingerprint(command)
        existing=self.db.execute('SELECT * FROM second_command WHERE id=?',(request_id,)).fetchone()
        if existing:
            if existing['request_hash']!=request_hash:raise SecondOutreachError('request_conflict',409)
            return json.loads(existing['result'])
        self.sync()
        with self.transaction():
            existing=self.db.execute('SELECT * FROM second_command WHERE id=?',(request_id,)).fetchone()
            if existing:
                if existing['request_hash']!=request_hash:raise SecondOutreachError('request_conflict',409)
                return json.loads(existing['result'])
            if kind=='refresh':result=self.status()
            elif kind=='render_batch':result=self._render_template_batch(command)
            else:
                row=self.get(command['opportunityId'])
                if type(command['expectedRevision']) is not int or row['revision']!=command['expectedRevision']:
                    raise SecondOutreachError('revision_conflict',409)
                if kind=='control':
                    if command['control'] not in ('active','paused'):raise SecondOutreachError('invalid_request')
                    if row['control']!=command['control']:
                        self.db.execute('UPDATE second_opportunity SET control=?,revision=revision+1 WHERE id=?',(command['control'],row['id']))
                    result=self.get(row['id'])
                else:
                    if row['control']=='paused':raise SecondOutreachError('relationship_suppressed',409)
                    if not row['currentRecipient']:raise SecondOutreachError('identity_unverified',409)
                    if kind=='render_template':
                        result=self._render_template(row,command['templateId'])
                    else:
                        existing_packet=self.db.execute('SELECT * FROM second_packet WHERE opportunity_id=? AND revision=? AND source_fingerprint=? ORDER BY created_at,id LIMIT 1',(row['id'],row['revision'],row['fingerprint'])).fetchone()
                        if existing_packet:
                            packet_id=existing_packet['id'];created=existing_packet['created_at']
                        else:
                            packet_id='second-packet-'+str(uuid.uuid4());created=self.clock()
                            self.db.execute('INSERT INTO second_packet VALUES(?,?,?,?,?,?)',(packet_id,row['id'],row['fingerprint'],row['revision'],canonical(row),created))
                        result={'packetId':packet_id,'opportunityId':row['id'],'sourceFingerprint':row['fingerprint'],'createdAt':created,'executionBlocked':True}
            self.db.execute('INSERT INTO second_command VALUES(?,?,?)',(request_id,request_hash,canonical(result)))
        return result

    def _render_template(self, row, template_id):
        """Caller holds the projection transaction and has checked the target."""
        from lib.second_templates import render_template
        rendered=render_template(template_id,row['currentRecipient']['handle'],row['products'],promotion_facts=self.promotion_facts())
        binding={'opportunityId':row['id'],'sourceFingerprint':row['fingerprint'],'revision':row['revision'],
                 'recipient':row['currentRecipient'],**rendered}
        context_hash=fingerprint(binding)
        saved=self.db.execute('SELECT payload FROM second_template_draft WHERE context_fingerprint=?',(context_hash,)).fetchone()
        if saved:result=json.loads(saved[0])
        else:
            created=self.clock();result={**binding,'draftId':'template-draft_'+context_hash,'contextFingerprint':context_hash,'createdAt':created,'executionBlocked':True}
            self.db.execute('INSERT INTO second_template_draft VALUES(?,?,?,?,?,?,?)',(result['draftId'],row['id'],row['revision'],row['fingerprint'],context_hash,canonical(result),created))
        self.db.execute('INSERT INTO second_template_selection VALUES(?,?) ON CONFLICT(opportunity_id) DO UPDATE SET draft_id=excluded.draft_id',(row['id'],result['draftId']))
        return result

    def _render_template_batch(self, command):
        """Freeze explicitly selected eligible targets; this does not enqueue sends."""
        from lib.second_templates import SecondTemplateError,list_templates
        meta=self.db.execute('SELECT source_fingerprint FROM second_meta WHERE id=1').fetchone()
        if meta['source_fingerprint']!=command['expectedSourceFingerprint']:raise SecondOutreachError('source_conflict',409)
        if not any(template['id']==command['templateId'] for template in list_templates()):raise SecondOutreachError('template_unknown',400)
        prepared,skipped,recipients=[],[],set()
        template_errors={'template_unknown','template_recipient_invalid','template_product_count_invalid','template_product_fields_invalid','template_product_name_unverified','template_duplicate_product','template_text_too_long','promotion_reason_missing'}
        for opportunity_id in command['opportunityIds']:
            record=self.db.execute('SELECT * FROM second_opportunity WHERE id=?',(opportunity_id,)).fetchone()
            reason=None
            if record is None:reason='opportunity_not_found'
            elif not record['active']:reason='source_inactive'
            else:
                row=self._public(record)
                recipient=row.get('currentRecipient')
                if row['control']=='paused':reason='relationship_suppressed'
                elif not recipient:reason='identity_ambiguous' if row.get('recipientStatus')=='ambiguous' else 'identity_unverified'
                elif (not isinstance(recipient,dict) or not isinstance(recipient.get('oecId'),str)
                      or not re.fullmatch(r'[0-9]{1,64}',recipient['oecId']) or int(recipient['oecId'])<=0
                      or not isinstance(recipient.get('creatorId'),str) or not re.fullmatch(r'creator_[0-9a-f]{32}',recipient['creatorId'])
                      or 'handle' not in recipient or row.get('recipientStatus','verified')!='verified'):
                    reason='identity_unverified'
                elif recipient['oecId'] in recipients:reason='duplicate_recipient'
                else:
                    try:
                        rendered=self._render_template(row,command['templateId'])
                    except SecondTemplateError as error:
                        reason=error.code if error.code in template_errors else 'template_render_failed'
                    else:
                        prepared.append(rendered);recipients.add(recipient['oecId'])
            if reason:skipped.append({'opportunityId':opportunity_id,'reason':reason})
        result={'templateId':command['templateId'],'prepared':prepared,'skipped':skipped,'sourceFingerprint':meta['source_fingerprint'],'modelCalls':0}
        return {'batchId':'template-batch_'+fingerprint({'scope':command['opportunityIds'],**result}),**result}

    def template_draft(self,draft_id,*,require_current=False):
        row=self.db.execute('SELECT payload FROM second_template_draft WHERE id=?',(token(draft_id),)).fetchone()
        if not row:raise SecondOutreachError('template_draft_missing',404)
        saved=json.loads(row[0])
        if require_current:
            from lib.second_templates import list_templates
            self.sync();current=self.get(saved['opportunityId'])
            template=next((t for t in list_templates() if t['id']==saved['templateId']),None)
            if current['control']=='paused':raise SecondOutreachError('relationship_suppressed',409)
            if current['revision']!=saved['revision'] or current['fingerprint']!=saved['sourceFingerprint'] or current['currentRecipient']!=saved['recipient'] or not template or template['fingerprint']!=saved['templateFingerprint']:
                raise SecondOutreachError('stale_context',409)
        return saved

    def context(self, request):
        from lib.outreach_drafts import normalize_context_request
        req=normalize_context_request(request)
        if not re.fullmatch(r'second-packet-[0-9a-f-]{36}',req['packetId']):raise SecondOutreachError('invalid_request')
        self.sync()
        saved=self.db.execute('SELECT * FROM second_packet WHERE id=?',(req['packetId'],)).fetchone()
        if not saved:raise SecondOutreachError('packet_missing',404)
        current=self.get(saved['opportunity_id'])
        if current['revision']!=saved['revision'] or current['fingerprint']!=saved['source_fingerprint']:
            raise SecondOutreachError('stale_context',409)
        if current['control']=='paused':raise SecondOutreachError('relationship_suppressed',409)
        recipient=current['currentRecipient']
        if not recipient:raise SecondOutreachError('identity_unverified',409)
        facts=[]
        if recipient['handle']:facts.append({'id':'recipient','kind':'recipient_handle','value':recipient['handle']})
        products=[];bindings=[]
        for index,p in enumerate(current['products'][:3],1):
            alias=f'p{index}'
            facts.append({'id':alias+'-name','kind':'product_name','value':p['nameIt']})
            products.append({'id':alias,'nameIt':p['nameIt'],'sharedCategories':[],'factIds':[alias+'-name']})
            bindings.append({'id':alias,'productId':p['id'],'pid':p['pid']})
        capsule={'schema':'bdhub.outreach-draft-context.v1','binding':{'origin':'second_outreach','market':'it',
                 'registryCreatorId':recipient['creatorId'],'oecId':recipient['oecId'],'packetId':req['packetId'],
                 'sourceOpportunityId':current['id'],'sourceFingerprint':current['fingerprint'],
                 'historicalOwnership':'unverified','policyVersion':'it-intent-invitation@1','skillSha256':hashlib.sha256(SKILL.read_bytes()).hexdigest()},
                 'modelFacts':{'intent':'explore_interest','language':'it','recipient':{'handle':recipient['handle']},
                 'style':req['style'],'instructions':req['instructions'],'products':products,'facts':facts,
                 'commercialTerms':{'status':'not_verified','allowedPromises':[]}},'productBindings':bindings,'executionBlocked':True}
        return {'context':capsule,'fingerprint':fingerprint(capsule),'contextRequest':req}
