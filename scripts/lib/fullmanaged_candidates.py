"""Append-only qualified PID evidence and references to existing selection intents.

Collection snapshots describe coverage. This projection preserves the first admission facts
without replacing the current-promotion checks or becoming a second selection state machine.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from lib.second_cycle import encoded

SCHEMA = '''
CREATE TABLE IF NOT EXISTS global_source_candidate(
 market TEXT NOT NULL, pid TEXT NOT NULL, source_run TEXT NOT NULL, screen_run TEXT NOT NULL,
 admitted_at REAL NOT NULL, observed_at REAL NOT NULL, evidence_json TEXT NOT NULL,
 PRIMARY KEY(market,pid));
CREATE INDEX IF NOT EXISTS global_candidate_source ON global_source_candidate(source_run);
CREATE TABLE IF NOT EXISTS global_candidate_publication(
 screen_run TEXT PRIMARY KEY, source_run TEXT NOT NULL, market TEXT NOT NULL,
 added INTEGER NOT NULL, total INTEGER NOT NULL, published_at REAL NOT NULL);
'''

SELECTION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS intake_candidate_owner(
 pid TEXT PRIMARY KEY, owner_run_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS intake_run_member(
 run_id TEXT NOT NULL, pid TEXT NOT NULL, owner_run_id TEXT NOT NULL,
 PRIMARY KEY(run_id,pid));
'''


def source_path(root, market):
    return Path(root)/('var/global-source.sqlite' if market=='it' else f'var/global-source-{market}.sqlite')


def admit_screen(db, screen_run, *, at):
    """Publish qualified evidence inside the caller's screening transaction.

    Only a completed source or an explicitly accepted historical partial scope can admit.
    All necessary qualification and page evidence is copied so normal retention remains safe.
    """
    if not db.in_transaction:
        raise ValueError('candidate_publication_transaction_required')
    columns={r[1] for r in db.execute('PRAGMA table_info(global_source_run)')}
    if not {'scope','identity_unchanged'} <= columns:
        return {'published':False,'reason':'source_evidence_incomplete'}
    prior=db.execute('SELECT added,total FROM global_candidate_publication WHERE screen_run=?',(screen_run,)).fetchone()
    if prior:return {'added':prior[0],'total':prior[1],'duplicate':True}
    row=db.execute('''SELECT s.source_run,s.config,r.scope,r.state,r.identity_unchanged
      FROM global_source_screen_run s JOIN global_source_run r ON r.id=s.source_run
      WHERE s.run_id=?''',(screen_run,)).fetchone()
    if not row or row[3] not in ('completed','accepted_partial') or not row[4]:
        return {'published':False,'reason':'source_not_publishable'}
    rid,config,raw_scope=row[:3];scope=json.loads(raw_scope);market=scope['market']
    from lib.global_source import SOURCE,FILTER
    if scope.get('source')!=SOURCE or scope.get('filter')!=FILTER:
        return {'published':False,'reason':'source_provenance_unverified'}
    existing={r[0] for r in db.execute('SELECT pid FROM global_source_candidate WHERE market=?',(market,))}
    added=0
    for product in db.execute('''SELECT p.pid,p.payload,p.fingerprint,p.first_page,p.observed,
      s.units,s.rating,s.total_bp,s.public_bp,s.gap_points,s.unrated,s.thresholds,s.config_fingerprint
      FROM global_source_screen s JOIN global_source_product p ON p.run_id=? AND p.pid=s.pid
      WHERE s.run_id=? AND s.state='eligible' ORDER BY p.pid''',(rid,screen_run)).fetchall():
        pid=product[0]
        if pid in existing:continue
        category_rows=db.execute('''SELECT c.category_id,c.category_name,c.first_page,p.response_hash,
          p.request_payload,p.observed FROM global_source_product_category c
          LEFT JOIN global_source_partition_page p ON p.run_id=c.run_id AND p.category_id=c.category_id
          AND p.page=c.first_page WHERE c.run_id=? AND c.pid=? ORDER BY c.category_id''',(rid,pid)).fetchall()
        page=db.execute('SELECT page,response_hash,observed FROM global_source_page WHERE run_id=? AND page=?',
                        (rid,product[3])).fetchone()
        facts={'sourceScope':{k:v for k,v in scope.items() if k!='categories'},'coverage':row[3],'product':json.loads(product[1]),
               'listingFingerprint':product[2],'firstPage':product[3],
               'pageEvidence':list(page) if page else None,'categoryEvidence':[list(r) for r in category_rows],
               'qualification':{'units':product[5],'rating':product[6],'totalBp':product[7],
                 'publicBp':product[8],'gapPoints':product[9],'unrated':bool(product[10]),
                 'thresholds':json.loads(product[11]),'configFingerprint':product[12]},
               'config':json.loads(config)}
        db.execute('INSERT INTO global_source_candidate VALUES(?,?,?,?,?,?,?)',
                   (market,pid,rid,screen_run,at,product[4],encoded(facts)))
        added+=1
    total=db.execute('SELECT count(*) FROM global_source_candidate WHERE market=?',(market,)).fetchone()[0]
    db.execute('INSERT INTO global_candidate_publication VALUES(?,?,?,?,?,?)',(screen_run,rid,market,added,total,at))
    return {'published':True,'added':added,'total':total,'duplicate':False}


def candidate_rows(root, market):
    path=source_path(root,market)
    if not path.exists():return []
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='global_source_candidate'").fetchone():
            return []
        return [dict(r)|{'evidence':json.loads(r['evidence_json'])} for r in db.execute(
            'SELECT * FROM global_source_candidate WHERE market=? ORDER BY admitted_at,pid',(market,))]


def selection_rows(db, run_id):
    """Original rows, never cloned intents; legacy runs keep their exact historical scope."""
    mapped=db.execute("SELECT 1 FROM sqlite_master WHERE name='intake_run_member'").fetchone()
    if mapped and db.execute('SELECT 1 FROM intake_run_member WHERE run_id=? LIMIT 1',(run_id,)).fetchone():
        return db.execute('''SELECT i.* FROM intake_run_member m JOIN intake_item i
          ON i.run_id=m.owner_run_id AND i.pid=m.pid WHERE m.run_id=? ORDER BY i.pid''',(run_id,)).fetchall()
    return db.execute('SELECT * FROM intake_item WHERE run_id=? ORDER BY pid',(run_id,)).fetchall()


def backfill_owners(db):
    """Bind pre-existing PIDs once, preserving original rows and all receipt history.

    Old generations sometimes copied unresolved intents. Prefer the newest outcome within an
    identical submitted intent; any unrelated unresolved intent wins over a fresh pending row.
    """
    owned={r[0] for r in db.execute('SELECT pid FROM intake_candidate_owner')}
    groups={}
    for row in db.execute('SELECT run_id,pid,state,payload,updated FROM intake_item ORDER BY updated DESC,run_id'):
        if row[1] not in owned:groups.setdefault(row[1],[]).append(row)
    unknown={'submitting','awaiting_verification','result_unknown','needs_review'}
    for pid,rows in groups.items():
        latest_by_intent={}
        for row in rows:
            payload=json.loads(row[3]);attempt=payload.get('attemptedAt')
            campaign=((payload.get('campaign') or {}).get('campaign') or {}).get('campaign_id')
            # A copied intent retains attemptedAt and frozen campaign. Without these facts,
            # separate rows are not silently treated as the same submission.
            key=(attempt,campaign) if attempt is not None and campaign else ('row',row[0])
            latest_by_intent.setdefault(key,row)
        candidates=list(latest_by_intent.values())
        owner=next((r for r in candidates if r[2] in unknown),None)
        if owner is None:
            owner=next((r for r in candidates if r[2]!='pending'),candidates[0])
        db.execute('INSERT INTO intake_candidate_owner VALUES(?,?)',(pid,owner[0]))


def migrate(root, market):
    """Local-only, additive migration. Back up source and selection databases before applying."""
    source=source_path(root,market)
    result={'market':market,'platformWrites':0,'admitted':0,'owners':0}
    if source.exists():
        with closing(sqlite3.connect(source,timeout=30)) as db:
            db.executescript(SCHEMA)
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            with db:
                db.execute('BEGIN IMMEDIATE')
                if 'global_source_screen_run' in tables:
                    for rid,created in db.execute('SELECT run_id,created FROM global_source_screen_run ORDER BY created,run_id').fetchall():
                        publication=admit_screen(db,rid,at=created)
                        if not publication.get('duplicate'):result['admitted']+=publication.get('added',0)
            result['candidates']=db.execute('SELECT count(*) FROM global_source_candidate WHERE market=?',(market,)).fetchone()[0]
    selection=Path(root)/('var/global-selection.sqlite' if market=='it' else f'var/global-selection-{market}.sqlite')
    if selection.exists():
        with closing(sqlite3.connect(selection,timeout=30)) as db:
            db.executescript(SELECTION_SCHEMA)
            before=db.execute('SELECT count(*) FROM intake_candidate_owner').fetchone()[0]
            with db:
                db.execute('BEGIN IMMEDIATE')
                backfill_owners(db)
            result['owners']=db.execute('SELECT count(*) FROM intake_candidate_owner').fetchone()[0]-before
            legacy=db.execute('''SELECT r.id,r.source_run,r.rules,r.created,i.pid,i.payload
              FROM intake_item i JOIN intake_run r ON r.id=i.run_id ORDER BY r.created,r.id,i.pid''').fetchall()
        if source.exists():
            # Retention may have removed an old listing/screen. Its original admitted intake
            # snapshot and frozen rules are still evidence; preserve it without rescreening.
            with closing(sqlite3.connect(source,timeout=30)) as db,db:
                db.execute('BEGIN IMMEDIATE')
                for run_id,rid,rules,created,pid,payload in legacy:
                    snapshot=json.loads(payload).get('snapshot') or {}
                    if snapshot.get('product_id')!=pid:continue
                    if db.execute('SELECT 1 FROM global_source_candidate WHERE market=? AND pid=?',(market,pid)).fetchone():continue
                    source_row=db.execute('SELECT scope FROM global_source_run WHERE id=?',(rid,)).fetchone()
                    scope=json.loads(source_row[0]) if source_row else {}
                    from lib.global_source import SOURCE,FILTER
                    if scope.get('market')!=market or scope.get('source')!=SOURCE or scope.get('filter')!=FILTER:continue
                    evidence={'sourceScope':{k:v for k,v in scope.items() if k!='categories'},
                              'product':snapshot,'config':json.loads(rules),
                              'qualification':{'historicalIntakeAccepted':True},
                              'legacyIntakeRun':run_id,'observedAtBasis':'original_intake_created',
                              'pageEvidence':None,'categoryEvidence':[]}
                    db.execute('INSERT INTO global_source_candidate VALUES(?,?,?,?,?,?,?)',
                               (market,pid,rid,'',created,created,encoded(evidence)))
                    result['admitted']+=1
                result['candidates']=db.execute('SELECT count(*) FROM global_source_candidate WHERE market=?',(market,)).fetchone()[0]
    return result


def candidate_summary(root,market,source_run=None):
    path=source_path(root,market)
    if not path.exists():return {'available':False}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='global_source_candidate'").fetchone():
            return {'available':False}
        total,first=db.execute('SELECT count(*),min(observed_at) FROM global_source_candidate WHERE market=?',(market,)).fetchone()
        added=db.execute('SELECT count(*) FROM global_source_candidate WHERE market=? AND source_run=?',(market,source_run)).fetchone()[0]
        return {'available':True,'total':total,'firstQualifiedAt':first,'addedInSource':added}
