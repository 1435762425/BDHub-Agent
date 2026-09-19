"""Catalog-level commission and link intent facts, independent of creator roster size."""
import json,re,sqlite3,time
from contextlib import closing
from decimal import Decimal,InvalidOperation
from pathlib import Path
from lib.second_cycle import digest,encoded

ROUTES={'selected','campaign'}
COMMISSION_KEYS=('version','agencyMinPoints','agencyPreferredPoints','newCreatorMinBoostPoints')

def policy_fingerprint(policy):
    """Fingerprint only commission semantics; historical-card handling is not a rate rule."""
    if not isinstance(policy,dict) or any(policy.get(key) is None for key in COMMISSION_KEYS):raise ValueError('catalog_policy_invalid')
    return digest({key:policy[key] for key in COMMISSION_KEYS})

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
    # Historical cards remain evidence only.  The caller may separately detect an exact standard
    # card by its frozen rule/name fingerprint, but an ordinary old card never becomes send material.
    if cards:return {'state':'historical_links_ignored'}
    return {'state':'may_prepare_creation'}

class CatalogLinks:
    def __init__(self,root):
        self.root=Path(root);self.policy=json.loads((self.root/'config/catalog-link-policy.json').read_text())
        self.db=sqlite3.connect(self.root/'var/catalog-links.sqlite',isolation_level=None,timeout=10);self.db.row_factory=sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS catalog_link_intent(id TEXT PRIMARY KEY,pid TEXT NOT NULL,account TEXT NOT NULL,state TEXT NOT NULL,spec TEXT NOT NULL,receipt TEXT,readback TEXT,created REAL NOT NULL,updated REAL NOT NULL)''')
        # 唯一键刻意保持**按 PID**（不是 PID×渠道）：一个商品永远只允许存在一份未结意图，
        # 跨渠道也不例外。这与"同一商品只出一条位置、全托优先"的商品级决定一致，
        # 也是防止同一商品被两条渠道各建一次链的兜底。真正的跨渠道重叠实测只有 1 个 PID，
        # 且它已有全托卡，非全托侧不建（见 catalog-link-prepare 的 route 排除）。
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
    def supersede_unsubmitted_legacy(self,pid,replacement_id):
        """Retire only legacy intents that provably never reached a platform call."""
        path=self.root/'var/second-cycle.sqlite'
        if not path.exists():return 0
        with closing(sqlite3.connect(path,timeout=10,isolation_level=None)) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_card_creation'").fetchone():return 0
            c.execute('BEGIN IMMEDIATE')
            try:
                if c.execute("SELECT 1 FROM cycle_card_creation WHERE pid=? AND state IN ('started','response_saved','unknown')",(pid,)).fetchone():
                    c.execute('ROLLBACK');return 0
                rows=c.execute("SELECT id FROM cycle_card_creation WHERE pid=? AND state='prepared' AND receipt IS NULL",(pid,)).fetchall()
                evidence=encoded({'reason':'superseded_by_catalog_standard','replacementId':replacement_id,
                                  'platformCreateAttempts':0})
                for row in rows:c.execute("UPDATE cycle_card_creation SET state='superseded',readback=? WHERE id=?",(evidence,row[0]))
                c.execute('COMMIT');return len(rows)
            except BaseException:
                c.execute('ROLLBACK');raise
    def supersede_redundant_prepared(self):
        """Close local-only catalog intents already satisfied by the exact current binding."""
        from lib.catalog_binding import CatalogBindings
        bindings=CatalogBindings(self.root,connection=self.db);settled=0
        for row in self.db.execute("SELECT id,spec FROM catalog_link_intent WHERE state='prepared' "
                                   "AND receipt IS NULL AND readback IS NULL").fetchall():
            try:spec=json.loads(row['spec']);binding=bindings.active_for_offer(spec.get('offer') or {})
            except (ValueError,TypeError):continue
            if not binding:continue
            evidence=encoded({'reason':'satisfied_by_current_binding','listId':binding['list_id'],
                              'platformCreateAttempts':0})
            settled+=self.db.execute("UPDATE catalog_link_intent SET state='superseded',readback=?,updated=? "
                                     "WHERE id=? AND state='prepared' AND receipt IS NULL",
                                     (evidence,time.time(),row['id'])).rowcount
        return settled
    def prepare(self,spec):
        """Freeze one creation intent. Purpose decides how strictly the spec is bound."""
        purpose=spec.get('purpose')
        if purpose=='acc9_single_card_canary':return self._freeze(spec,'catalog_link_canary_scope_invalid',canary=True)
        if purpose!='catalog_batch_link':raise ValueError('catalog_link_purpose_invalid')
        if not str(spec.get('campaignId','')).isdigit() or not str(spec.get('pid','')).isdigit() or not str(spec.get('sourceRun','')):raise ValueError('catalog_prepare_binding_invalid')
        if not isinstance(spec.get('shortName'),str) or not 1<=len(spec['shortName'])<=30:raise ValueError('catalog_prepare_name_invalid')
        return self._freeze(spec,'catalog_link_scope_invalid',canary=False)
    def _freeze(self,spec,scope_error,*,canary):
        # 渠道属于商品/线索/链接：同一个 PID 在两条渠道上是两个不同的平台对象，
        # 所以这里放开 route，但载荷必须逐字节等于**该渠道**的冻结请求。
        if spec.get('account')!='acc9' or spec.get('market')!='it' or spec.get('route') not in ROUTES:raise ValueError(scope_error)
        if canary:
            if type(spec.get('searchTotal')) is not int or spec['searchTotal']!=0:raise ValueError('existing_link_or_intent_requires_review')
        elif type(spec.get('searchTotal')) is not int or spec['searchTotal']<0 or spec.get('standardSearchComplete') is not True:
            raise ValueError('catalog_standard_search_required')
        if spec.get('policyFingerprint')!=policy_fingerprint(self.policy):raise ValueError('catalog_policy_changed')
        if not canary:
            if spec.get('policyVersion')!=self.policy.get('version') or spec.get('namingVersion')!='link-naming-v1' or not re.fullmatch(r'[0-9a-f]{64}',str(spec.get('namingFingerprint') or '')):raise ValueError('catalog_standard_rule_invalid')
        # 预期载荷由生成器本身给出，不再手抄一份：抄的那份在加渠道时正是最容易抄错的地方。
        from bdhub.send.taplink.protocol import create_payload
        try:expected=create_payload(pid=spec['pid'],campaign_id=spec['campaignId'],creator_pct=spec['creatorPercent'],
                                    name=spec['listName'],route=spec['route'])
        except ValueError:raise ValueError('catalog_payload_binding_invalid') from None
        if spec.get('payload')!=expected or not 1<=len(spec['listName'])<=50:raise ValueError('catalog_payload_binding_invalid')
        key={k:spec[k] for k in ('pid','account','market','route','campaignId','creatorPercent','policyFingerprint')}
        if not canary:
            from lib.catalog_binding import offer_fingerprint
            key['offerFingerprint']=offer_fingerprint(spec.get('offer'))
            key['namingFingerprint']=spec['namingFingerprint']
        id='catalog-link-'+digest(key)[:28]
        if not canary:self.supersede_unsubmitted_legacy(spec['pid'],id)
        if self.legacy_conflict(spec['pid']):raise ValueError('legacy_creation_in_progress' if not canary else 'existing_link_or_intent_requires_review')
        old=self.db.execute('SELECT id FROM catalog_link_intent WHERE id=?',(id,)).fetchone()
        if old:return self.get(id)
        try:self.db.execute('INSERT INTO catalog_link_intent VALUES(?,?,?,?,?,NULL,NULL,?,?)',(id,spec['pid'],spec['account'],'prepared',encoded(spec),time.time(),time.time()))
        except sqlite3.IntegrityError:raise ValueError('catalog_link_intent_in_progress') from None
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
        return self._confirm(id,card,allowed=('submitted','receipt_saved','unknown'),existing=False)
    def confirm_existing_standard(self,id,card):
        """Settle a frozen intent from a complete read that found the exact standard card."""
        return self._confirm(id,card,allowed=('prepared',),existing=True)
    def _confirm(self,id,card,*,allowed,existing):
        row=self.get(id);s=row['spec']
        if row['state']=='verified':return self.get(id)
        if row['state'] not in allowed:raise ValueError('catalog_link_not_submitted')
        if not re.fullmatch(r'[1-9][0-9]{0,31}',str(card.get('listId',''))):raise ValueError('catalog_link_id_missing')
        # 回读卡片的线上活动号按渠道判定：全托是账号级卡片（campaign_id='0'），
        # 非全托卡片就挂在这个活动上。用错渠道的期望值会让确认整批失败。
        wire='0' if s['route']=='selected' else s['campaignId']
        if any(str(card.get(k))!=str(v) for k,v in {'pid':s['pid'],'sourceCampaignId':s['campaignId'],'creatorPercent':s['creatorPercent'],'wireCampaignId':wire,'verifiedListName':s['listName'],'state':'verified_read_only'}.items()):raise ValueError('catalog_link_binding_mismatch')
        if not existing and row['receipt'] and row['receipt'].get('list_id') and row['receipt']['list_id']!=card.get('listId'):raise ValueError('catalog_link_receipt_mismatch')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if s.get('purpose')=='catalog_batch_link':
                from lib.catalog_binding import CatalogBindings
                CatalogBindings(self.root,connection=self.db).promote(s,card,id)
            self.db.execute("UPDATE catalog_link_intent SET state='verified',readback=?,updated=? WHERE id=?",(encoded(card),time.time(),id))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        return self.get(id)

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
