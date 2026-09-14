"""Catalog-level commission and link intent facts, independent of creator roster size."""
import json,re,sqlite3,time
from contextlib import closing
from decimal import Decimal,InvalidOperation
from pathlib import Path
from lib.second_cycle import digest,encoded

def basis(value):
    if value is None or isinstance(value,bool):raise ValueError('commission_missing')
    try:d=Decimal(str(value))
    except InvalidOperation:raise ValueError('commission_invalid') from None
    if not d.is_finite() or not 0<=d<=10000 or d!=d.to_integral_value():raise ValueError('commission_precision_invalid')
    return int(d)

def new_commission(total,public,policy):
    total,public=basis(total),basis(public)
    minimum=basis(Decimal(policy['agencyMinPoints'])*100);preferred=basis(Decimal(policy['agencyPreferredPoints'])*100);boost=basis(Decimal(policy['newCreatorMinBoostPoints'])*100)
    if minimum>preferred or total-public<minimum+boost:raise ValueError('insufficient_commission_gap')
    agency=min(preferred,total-public-boost);creator=total-agency
    return {'totalRaw':total,'publicRaw':public,'creatorRaw':creator,'agencyRaw':agency,'creatorPercent':str(Decimal(creator)/100),'agencyPercent':str(Decimal(agency)/100)}

def reusable_old(card,policy):
    try:
        total,public,creator=(basis(card[k]) for k in ('totalRaw','publicRaw','creatorRaw'))
        return card.get('platformValid') is True and card.get('productEligible') is True and creator>public and total-creator>=Decimal(policy['agencyMinPoints'])*100
    except (ValueError,KeyError):return False

def choose_existing(cards,policy):
    eligible=[c for c in cards if reusable_old(c,policy)]
    return max(eligible,key=lambda c:(basis(c['creatorRaw']),c.get('previouslyUsed') is True,str(c.get('listId','')))) if eligible else None

def link_decision(cards,policy,*,search_complete):
    if not search_complete:return {'state':'lookup_incomplete'}
    selected=choose_existing(cards,policy)
    if selected:return {'state':'reuse','card':selected}
    if cards:return {'state':'old_links_require_review'}
    return {'state':'may_prepare_creation'}

class CatalogLinks:
    def __init__(self,root):
        self.root=Path(root);self.policy=json.loads((self.root/'config/catalog-link-policy.json').read_text())
        self.db=sqlite3.connect(self.root/'var/catalog-links.sqlite',isolation_level=None,timeout=10);self.db.row_factory=sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS catalog_link_intent(id TEXT PRIMARY KEY,pid TEXT NOT NULL,account TEXT NOT NULL,state TEXT NOT NULL,spec TEXT NOT NULL,receipt TEXT,readback TEXT,created REAL NOT NULL,updated REAL NOT NULL)''')
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS catalog_link_open_pid ON catalog_link_intent(pid) WHERE state IN ('prepared','submitted','receipt_saved','unknown')")
    def get(self,id):
        r=self.db.execute('SELECT * FROM catalog_link_intent WHERE id=?',(id,)).fetchone()
        if not r:raise ValueError('catalog_link_intent_missing')
        return dict(r)|{'spec':json.loads(r['spec']),'receipt':json.loads(r['receipt']) if r['receipt'] else None,'readback':json.loads(r['readback']) if r['readback'] else None}
    def legacy_conflict(self,pid):
        path=self.root/'var/second-cycle.sqlite'
        if not path.exists():return False
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_card_creation'").fetchone():return False
            return bool(c.execute("SELECT 1 FROM cycle_card_creation WHERE pid=? AND state IN ('prepared','started','response_saved','unknown')",(pid,)).fetchone())
    def prepare(self,spec):
        if spec.get('account')!='acc9' or spec.get('market')!='it' or spec.get('route')!='selected' or spec.get('purpose')!='acc9_single_card_canary':raise ValueError('catalog_link_canary_scope_invalid')
        if type(spec.get('searchTotal')) is not int or spec['searchTotal']!=0 or self.legacy_conflict(spec['pid']):raise ValueError('existing_link_or_intent_requires_review')
        if spec.get('policyFingerprint')!=digest(self.policy):raise ValueError('catalog_policy_changed')
        rate=basis(Decimal(spec['creatorPercent'])*100)
        expected={'name':spec['listName'],'campaign_id':'0','source':2,'items':[{'product_id':spec['pid'],'campaign_id':spec['campaignId'],'creator_commission_rate':str(rate)}]}
        if spec.get('payload')!=expected or not 1<=len(spec['listName'])<=50:raise ValueError('catalog_payload_binding_invalid')
        key={k:spec[k] for k in ('pid','account','market','route','campaignId','creatorPercent','policyFingerprint')};id='catalog-link-'+digest(key)[:28]
        old=self.db.execute('SELECT id FROM catalog_link_intent WHERE id=?',(id,)).fetchone()
        if old:return self.get(id)
        if self.db.execute('SELECT 1 FROM catalog_link_intent WHERE pid=?',(spec['pid'],)).fetchone():raise ValueError('prior_catalog_link_requires_review')
        self.db.execute('INSERT INTO catalog_link_intent VALUES(?,?,?,?,?,NULL,NULL,?,?)',(id,spec['pid'],spec['account'],'prepared',encoded(spec),time.time(),time.time()))
        return self.get(id)
    def begin(self,id,account):
        if self.get(id)['account']!=account:raise ValueError('catalog_link_account_changed')
        if self.legacy_conflict(self.get(id)['pid']):raise ValueError('legacy_creation_in_progress')
        n=self.db.execute("UPDATE catalog_link_intent SET state='submitted',updated=? WHERE id=? AND state='prepared'",(time.time(),id)).rowcount
        if n!=1:raise ValueError('catalog_link_already_submitted')
    def receipt(self,id,value):
        n=self.db.execute("UPDATE catalog_link_intent SET state='receipt_saved',receipt=?,updated=? WHERE id=? AND state='submitted'",(encoded(value),time.time(),id)).rowcount
        if n!=1:raise ValueError('catalog_link_receipt_state')
    def unknown(self,id,reason):
        self.db.execute("UPDATE catalog_link_intent SET state='unknown',readback=?,updated=? WHERE id=? AND state IN ('submitted','receipt_saved','unknown')",(encoded({'reason':reason}),time.time(),id))
    def confirm(self,id,card):
        row=self.get(id);s=row['spec']
        if row['state']=='verified':return
        if row['state'] not in ('submitted','receipt_saved','unknown'):raise ValueError('catalog_link_not_submitted')
        if not re.fullmatch(r'[1-9][0-9]{0,31}',str(card.get('listId',''))):raise ValueError('catalog_link_id_missing')
        if any(str(card.get(k))!=str(v) for k,v in {'pid':s['pid'],'sourceCampaignId':s['campaignId'],'creatorPercent':s['creatorPercent'],'wireCampaignId':'0','verifiedListName':s['listName'],'state':'verified_read_only'}.items()):raise ValueError('catalog_link_binding_mismatch')
        if row['receipt'] and row['receipt'].get('list_id') and row['receipt']['list_id']!=card.get('listId'):raise ValueError('catalog_link_receipt_mismatch')
        self.db.execute("UPDATE catalog_link_intent SET state='verified',readback=?,updated=? WHERE id=?",(encoded(card),time.time(),id))

def catalog_owns_pid(cycle_db,pid):
    files=cycle_db.execute('PRAGMA database_list').fetchall();main=next((r[2] for r in files if r[1]=='main'),None)
    if not main:return False
    path=Path(main).parent/'catalog-links.sqlite'
    if not path.exists():return False
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
        return bool(c.execute("SELECT 1 FROM catalog_link_intent WHERE pid=? AND state IN ('prepared','submitted','receipt_saved','unknown','verified')",(pid,)).fetchone())

def canary_summary(root,refs):
    if not refs:return None
    root=Path(root);reports=[]
    for key in ('creation','senderReadback'):
        path=(root/refs[key]).resolve()
        if not path.is_relative_to((root/'var').resolve()):raise ValueError('link_evidence_path_invalid')
        if not path.exists():return {'state':'not_verified'}
        reports.append(json.loads(path.read_text()))
    created,seen=reports
    if any(r.get('state')!='verified' or r.get('identityFileUnchanged') is not True or r.get('realSends')!=0 for r in reports):return {'state':'not_verified'}
    if created.get('platformWrites')!=1 or seen.get('platformWrites')!=0 or created['scope'].get('account')!='acc9' or seen['scope'].get('account')!='acc6':return {'state':'not_verified'}
    if created['scope'].get('market')!='it' or seen['scope'].get('market')!='it' or created['scope'].get('institutionFingerprint')!=seen['scope'].get('institutionFingerprint'):return {'state':'not_verified'}
    if created.get('intentId')!=seen.get('intentId') or any(created['card'].get(k)!=seen['card'].get(k) for k in ('pid','listId','creatorPercent','sourceCampaignId')):return {'state':'not_verified'}
    path=root/'var/catalog-links.sqlite'
    if not path.exists():return {'state':'not_verified'}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
        row=c.execute('SELECT state,spec,readback FROM catalog_link_intent WHERE id=?',(created['intentId'],)).fetchone()
    if not row or row[0]!='verified' or json.loads(row[2]).get('listId')!=created['card']['listId']:return {'state':'not_verified'}
    s=json.loads(row[1]);return {'state':'verified','pid':s['pid'],'shortName':s['shortName'],'listId':created['card']['listId'],
        'creatorPercent':s['creatorPercent'],'publicPercent':s['offer']['publicPercent'],'totalPercent':s['offer']['totalPercent'],
        'agencyPercent':str(Decimal(s['offer']['totalPercent'])-Decimal(s['creatorPercent'])),'creatorAccount':'acc9','senderReaderAccount':'acc6','checkedAt':seen['card']['checkedAt']}
