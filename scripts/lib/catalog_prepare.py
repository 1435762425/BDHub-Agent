"""Resumable catalog-wide PID link preparation, independent of any creator roster.

State per (pid, campaign, source): read -> reuse / review / missing -> intent ->
ready. Roster size never gates this queue; the same durable ledger and creation
lock used by the single-card canary is shared so no second link is created.
"""
import json,re,sqlite3,time
from contextlib import closing
from decimal import Decimal,InvalidOperation
from pathlib import Path
from lib.second_cycle import digest,encoded
from lib.product_stock_policy import require_stock,unavailable_allowed

CARD='/api/v1/affiliate/partner/im/product_list/list'
MEMBERS='/api/v1/affiliate/partner/campaign/product_list/products'
LISTS='/api/v1/affiliate/partner/campaign/product_list/list'
READ_EXTRA={(CARD,'GET'),(MEMBERS,'GET'),(LISTS,'GET')}

SCHEMA='''
CREATE TABLE IF NOT EXISTS catalog_prepare_run(id TEXT PRIMARY KEY,institution TEXT NOT NULL,market TEXT NOT NULL,source_run TEXT,scope TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS catalog_prepare_item(
 run_id TEXT NOT NULL,pid TEXT NOT NULL,campaign_id TEXT NOT NULL,catalog_source TEXT NOT NULL,
 state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,fence INTEGER NOT NULL DEFAULT 0,
 title TEXT,creator_percent TEXT,public_percent TEXT,total_percent TEXT,end_at TEXT,
 listing TEXT,card TEXT,blocker TEXT,error TEXT,intent_id TEXT,read_at REAL,updated REAL NOT NULL,
 PRIMARY KEY(run_id,pid,campaign_id,catalog_source));
CREATE INDEX IF NOT EXISTS catalog_prepare_item_state ON catalog_prepare_item(state);
CREATE TABLE IF NOT EXISTS catalog_prepare_reuse(
 run_id TEXT NOT NULL,pid TEXT NOT NULL,campaign_id TEXT NOT NULL,catalog_source TEXT NOT NULL,list_id TEXT NOT NULL,
 creator_percent TEXT NOT NULL,public_percent TEXT,reusable INTEGER NOT NULL,reason TEXT,observed_at REAL NOT NULL,
 PRIMARY KEY(run_id,pid,campaign_id,catalog_source,list_id));
CREATE TABLE IF NOT EXISTS catalog_prepare_readback(
 run_id TEXT NOT NULL,pid TEXT NOT NULL,campaign_id TEXT NOT NULL,catalog_source TEXT NOT NULL,kind TEXT NOT NULL,
 payload TEXT NOT NULL,observed_at REAL NOT NULL,PRIMARY KEY(run_id,pid,campaign_id,catalog_source,kind));
CREATE TABLE IF NOT EXISTS catalog_tap_list(
 list_id TEXT PRIMARY KEY,source TEXT NOT NULL,campaign_id TEXT NOT NULL,name TEXT,url TEXT,
 product_total INTEGER,platform_updated_at TEXT,observed REAL NOT NULL,members_at REAL);
CREATE TABLE IF NOT EXISTS catalog_tap_member(
 list_id TEXT NOT NULL,pid TEXT NOT NULL,member_campaign_id TEXT,creator_percent TEXT,public_percent TEXT,
 product_status TEXT,governed INTEGER,unavailable_type TEXT,stock TEXT,list_name TEXT,
 observed REAL NOT NULL,PRIMARY KEY(list_id,pid));
CREATE INDEX IF NOT EXISTS catalog_tap_member_pid ON catalog_tap_member(pid);
'''

def percent_raw(value):
    if value is None or isinstance(value,bool):return None
    try:d=Decimal(str(value))
    except InvalidOperation:return None
    if not d.is_finite() or not 0<=d<=10000:return None
    return d

def rate_text(raw):
    d=Decimal(str(raw))
    return format(d/100,'f')

def assess_existing(card,policy):
    """Existing platform link usable for second outreach: actual binding, valid, creator>public, agency>=min."""
    try:
        creator=Decimal(str(card['creatorRaw']))/100;public=Decimal(str(card['publicRaw']))/100
        agency=Decimal(str(card['totalRaw']))/100-creator
    except (KeyError,InvalidOperation,TypeError):return False,'link_facts_missing'
    if card.get('platformValid') is not True:return False,'link_not_platform_valid'
    if card.get('productEligible') is not True:return False,'link_product_not_eligible'
    if creator<=public:return False,'link_creator_not_above_public'
    if agency<Decimal(policy['agencyMinPoints']):return False,'link_agency_below_minimum'
    return True,None

def choose_existing_batch(cards,policy):
    accepted=[]
    for c in cards:
        ok,reason=assess_existing(c,policy)
        if ok:accepted.append(c)
    if not accepted:return None
    return max(accepted,key=lambda c:(Decimal(str(c['creatorRaw'])),c.get('previouslyUsed') is True,str(c.get('listId',''))))

class CatalogPreparation:
    def __init__(self,root):
        self.root=Path(root)
        self.ledger_db=Path(root)/'var/catalog-links.sqlite'
        self.db=sqlite3.connect(self.ledger_db,timeout=15);self.db.row_factory=sqlite3.Row
        self.db.executescript(SCHEMA)
        # Older rows may carry no creator rate when the binding itself did not match.
        if dict(self.db.execute("SELECT name,type FROM pragma_table_info('catalog_prepare_reuse')").fetchall()).get('creator_percent') and \
           self.db.execute("SELECT \"notnull\" FROM pragma_table_info('catalog_prepare_reuse') WHERE name='creator_percent'").fetchone()[0]:
            self.db.executescript('''ALTER TABLE catalog_prepare_reuse RENAME TO catalog_prepare_reuse_old;
CREATE TABLE catalog_prepare_reuse(
 run_id TEXT NOT NULL,pid TEXT NOT NULL,campaign_id TEXT NOT NULL,catalog_source TEXT NOT NULL,list_id TEXT NOT NULL,
 creator_percent TEXT,public_percent TEXT,reusable INTEGER NOT NULL,reason TEXT,observed_at REAL NOT NULL,
 PRIMARY KEY(run_id,pid,campaign_id,catalog_source,list_id));
INSERT OR IGNORE INTO catalog_prepare_reuse SELECT run_id,pid,campaign_id,catalog_source,list_id,creator_percent,public_percent,reusable,reason,observed_at FROM catalog_prepare_reuse_old;
DROP TABLE catalog_prepare_reuse_old;''')
        self.policy=json.loads((self.root/'config/catalog-link-policy.json').read_text())
    def close(self):self.db.close()
    # ---- run and item lifecycle -------------------------------------------------
    def run_id(self,scope):
        return 'catalog-prepare-'+digest(scope)[:24]
    def open_run(self,scope):
        if not isinstance(scope,dict) or scope.get('market')!='it' or scope.get('account')!='acc9':raise ValueError('catalog_prepare_scope_invalid')
        id=self.run_id(scope)
        with self.db:self.db.execute('INSERT OR IGNORE INTO catalog_prepare_run VALUES(?,?,?,?,?,?)',(id,scope.get('institution'),scope['market'],scope.get('sourceRun'),encoded(scope),time.time()))
        return id
    def seed(self,run_id,items,scope):
        """Insert untouched PIDs as pending; never rewrites a classified row."""
        added=0
        with self.db:
            for it in items:
                pid=str(it['pid']);cid=str(it.get('campaignId') or '');src=it.get('catalogSource') or 'selected'
                if not pid.isdigit() or not cid.isdigit() or src not in ('selected','campaign'):raise ValueError('catalog_prepare_item_invalid')
                added+=self.db.execute("INSERT OR IGNORE INTO catalog_prepare_item(run_id,pid,campaign_id,catalog_source,state,title,updated) VALUES(?,?,?,?,'pending',?,?)",
                    (run_id,pid,cid,src,str(it.get('title') or '')[:500],time.time())).rowcount
        return added
    def item(self,run_id,pid,cid,src='selected'):
        r=self.db.execute('SELECT * FROM catalog_prepare_item WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=?',(run_id,str(pid),str(cid),src)).fetchone()
        if not r:raise ValueError('catalog_prepare_item_missing')
        return dict(r)|{'listing':json.loads(r['listing']) if r['listing'] else None,'card':json.loads(r['card']) if r['card'] else None}
    def claim_read(self,run_id,now=None,lease=180,limit=15):
        """Claim pending read work only; creation claims have their own lease."""
        now=now if now is not None else time.time();claimed=[]
        with self.db:
            rows=self.db.execute("SELECT * FROM catalog_prepare_item WHERE run_id=? AND (state='pending' OR (state IN ('read_incomplete','reading','prepared') AND lease_until<=?)) ORDER BY updated,pid LIMIT ?",(run_id,now,limit)).fetchall()
            for r in rows:
                n=self.db.execute("UPDATE catalog_prepare_item SET state='reading',lease_until=?,fence=fence+1,updated=? WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=? AND fence=?",
                    (now+lease,now,run_id,r['pid'],r['campaign_id'],r['catalog_source'],r['fence'])).rowcount
                if n==1:claimed.append(self.item(run_id,r['pid'],r['campaign_id'],r['catalog_source']))
        return claimed
    def apply_read(self,run_id,pid,cid,src,outcome,now=None):
        """outcome: {state, listing, card, blocker, error, reuse:[...]}"""
        now=now if now is not None else time.time();state=outcome['state']
        if state not in ('reuse','review','missing','read_incomplete'):raise ValueError('catalog_prepare_state_invalid')
        listing=outcome.get('listing') or {}
        with self.db:
            self.db.execute("UPDATE catalog_prepare_item SET state=?,attempts=attempts+1,lease_until=0,title=?,creator_percent=?,public_percent=?,total_percent=?,end_at=?,listing=?,card=?,blocker=?,error=?,read_at=?,updated=? WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=?",
                (state,str(listing.get('title') or outcome.get('title') or '')[:500],listing.get('creatorPercent'),listing.get('publicPercent'),listing.get('totalPercent'),listing.get('endAt'),
                 encoded(listing) if listing else None,encoded(outcome.get('card')) if outcome.get('card') else None,outcome.get('blocker'),outcome.get('error'),now,now,run_id,str(pid),str(cid),src))
            for c in outcome.get('reuse') or []:
                self.db.execute('INSERT OR REPLACE INTO catalog_prepare_reuse VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (run_id,str(pid),str(cid),src,str(c['listId']),str(c['creatorRaw']) if c.get('creatorRaw') is not None else None,c.get('publicRaw'),1 if c.get('reusable') else 0,c.get('reason'),now))
    def reuse_rows(self,run_id,pid,cid,src):
        return [dict(r) for r in self.db.execute('SELECT * FROM catalog_prepare_reuse WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=? ORDER BY observed_at DESC,list_id',(run_id,str(pid),str(cid),src))]
    # ---- freeze and execute -----------------------------------------------------
    def freeze(self,run_id,pid,cid,src,spec,now=None):
        """Freeze a creation intent for a confirmed-missing PID; idempotent per business key."""
        now=now if now is not None else time.time()
        row=self.item(run_id,pid,cid,src)
        if row['state'] not in ('missing','prepared','submitted','unknown'):raise ValueError('catalog_prepare_not_missing')
        if not row['card']:
            accept={'pid':str(pid),'campaignId':str(cid),'catalogSource':src,'searchTotal':0,'state':'confirmed_missing'}
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO catalog_prepare_readback VALUES(?,?,?,?,?,?,?)',(run_id,str(pid),str(cid),src,'missingProof',encoded(self._missing_proof(row)),now))
                self.db.execute("UPDATE catalog_prepare_item SET card=?,updated=? WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=?",(encoded(accept),now,run_id,str(pid),str(cid),src))
        from lib.catalog_links import CatalogLinks
        ledger=CatalogLinks(self.root)
        try:
            intent=ledger.prepare(spec)
        finally:
            ledger.db.close()
        with self.db:
            self.db.execute("UPDATE catalog_prepare_item SET state='prepared',intent_id=?,creator_percent=?,public_percent=?,total_percent=?,updated=? WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=?",
                (intent['id'],spec['creatorPercent'],spec['offer']['publicPercent'],spec['offer']['totalPercent'],now,run_id,str(pid),str(cid),src))
        return intent
    def _missing_proof(self,row):
        listing=row['listing']
        if isinstance(listing,str):listing=json.loads(listing) if listing else {}
        return {'state':'confirmed_missing','pid':row['pid'],'campaignId':row['campaign_id'],'catalogSource':row['catalog_source'],
                'search':json.loads(row['card']) if isinstance(row['card'],str) and row['card'] else row['card'],'listing':listing or {},'readAt':row['read_at']}
    def mark_progress(self,run_id,pid,cid,src,state,*,card=None,error=None,now=None):
        now=now if now is not None else time.time()
        if state not in ('reuse','review','missing','ready','submitted','unknown'):raise ValueError('catalog_prepare_state_invalid')
        with self.db:
            self.db.execute("UPDATE catalog_prepare_item SET state=?,card=COALESCE(?,card),error=?,lease_until=0,updated=? WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=?",
                (state,encoded(card) if card else None,error,now,run_id,str(pid),str(cid),src))
            if state=='ready' and card:
                self.db.execute('INSERT OR REPLACE INTO catalog_prepare_readback VALUES(?,?,?,?,?,?,?)',(run_id,str(pid),str(cid),src,'verifiedLink',encoded(card),now))
    def claim_create(self,run_id,now=None,lease=300):
        now=now if now is not None else time.time()
        with self.db:
            r=self.db.execute("SELECT * FROM catalog_prepare_item WHERE run_id=? AND state IN ('missing','prepared','submitted','unknown') AND lease_until<=? ORDER BY CASE state WHEN 'prepared' THEN 0 WHEN 'missing' THEN 1 WHEN 'submitted' THEN 2 ELSE 3 END,updated,pid LIMIT 1",(run_id,now)).fetchone()
            if not r:return None
            n=self.db.execute("UPDATE catalog_prepare_item SET lease_until=?,fence=fence+1,updated=? WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=? AND fence=?",
                (now+lease,now,run_id,r['pid'],r['campaign_id'],r['catalog_source'],r['fence'])).rowcount
            if n!=1:return None
        return self.item(run_id,r['pid'],r['campaign_id'],r['catalog_source'])
    def release(self,run_id,pid,cid,src):
        with self.db:self.db.execute("UPDATE catalog_prepare_item SET lease_until=0,updated=? WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=?",(time.time(),run_id,str(pid),str(cid),src))
    # ---- read contracts ---------------------------------------------------------
    def verified_link(self,pid,campaign_id,catalog_source):
        """Consumer contract: exact verified link for one product plan, else None with a reason."""
        row=self.db.execute("SELECT * FROM catalog_prepare_readback WHERE pid=? AND campaign_id=? AND catalog_source=? AND kind='verifiedLink' ORDER BY observed_at DESC LIMIT 1",(str(pid),str(campaign_id),catalog_source)).fetchone()
        if not row:return None
        card=json.loads(row['payload'])
        return card|{'contract':{'pid':str(pid),'campaignId':str(campaign_id),'catalogSource':catalog_source,'verifiedAt':row['observed_at']}}
    def offer_status(self,offer):
        """Read-only view for one offer: ready | pending | blocked with a precise reason."""
        rows=self.db.execute("SELECT state,blocker,error,intent_id,updated FROM catalog_prepare_item WHERE pid=? AND campaign_id=? AND catalog_source=? ORDER BY updated DESC",(str(offer['pid']),str(offer.get('campaignId')),offer.get('catalogSource'))).fetchall()
        if not rows:return {'state':'unprepared','reason':'catalog_link_not_prepared'}
        card=self.verified_link(offer['pid'],offer.get('campaignId'),offer.get('catalogSource'))
        if card and card.get('creatorPercent')==offer.get('creatorPercent'):return {'state':'ready','card':card}
        if card:return {'state':'blocked','reason':'catalog_link_terms_changed','cardCreatorPercent':card.get('creatorPercent')}
        best=rows[0]
        reason={'missing':'catalog_link_creation_pending','prepared':'catalog_link_creation_pending','submitted':'catalog_link_creation_unresolved','unknown':'catalog_link_creation_unresolved',
                'reading':'catalog_link_lookup_running','pending':'catalog_link_lookup_pending','read_incomplete':'catalog_link_lookup_incomplete',
                'reuse':'catalog_link_reuse_missing','review':'catalog_link_requires_review'}.get(best['state'],'catalog_link_'+str(best['state']))
        return {'state':'pending' if best['state'] in ('pending','reading','missing','prepared') else 'blocked','reason':best['error'] or best['blocker'] or reason,'prepareState':best['state'],'intentId':best['intent_id']}
    def retire_stale_bindings(self,run_id,live_pairs,now=None):
        """Rows whose campaign binding the platform no longer returns are retired, never counted as gaps."""
        now=now if now is not None else time.time();retired=0
        keep={(str(p),str(c)) for p,c in live_pairs}
        with self.db:
            for r in self.db.execute("SELECT pid,campaign_id,catalog_source,state FROM catalog_prepare_item WHERE run_id=? AND state NOT IN ('retired','ready')",(run_id,)).fetchall():
                if (r['pid'],r['campaign_id']) in keep:continue
                retired+=self.db.execute("UPDATE catalog_prepare_item SET state='retired',blocker='superseded_by_live_binding',lease_until=0,updated=? WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=?",
                    (now,run_id,r['pid'],r['campaign_id'],r['catalog_source'])).rowcount
        return retired
    def retire_mismatched_bindings(self,run_id,now=None):
        """A row whose campaign binding the live pool no longer returns is superseded, not a gap."""
        now=now if now is not None else time.time()
        with self.db:
            return self.db.execute("UPDATE catalog_prepare_item SET state='retired',blocker='superseded_by_live_binding',lease_until=0,updated=? WHERE run_id=? AND state<>'ready' AND error='selected_campaign_changed'",(now,run_id)).rowcount
    def summary(self,run_id):
        counts={r[0]:r[1] for r in self.db.execute("SELECT state,count(*) FROM catalog_prepare_item WHERE run_id=? AND state<>'retired' GROUP BY state",(run_id,))}
        links=self.db.execute("SELECT count(*),count(DISTINCT pid) FROM catalog_prepare_readback WHERE run_id=? AND kind='verifiedLink'",(run_id,)).fetchone()
        retired=self.db.execute("SELECT count(*) FROM catalog_prepare_item WHERE run_id=? AND state='retired'",(run_id,)).fetchone()[0]
        reuse=self.db.execute('SELECT count(DISTINCT pid) FROM catalog_prepare_reuse WHERE run_id=? AND reusable=1',(run_id,)).fetchone()[0]
        errors=[r[0] for r in self.db.execute('SELECT DISTINCT error FROM catalog_prepare_item WHERE run_id=? AND error IS NOT NULL',(run_id,))]
        blockers=[r[0] for r in self.db.execute('SELECT DISTINCT blocker FROM catalog_prepare_item WHERE run_id=? AND blocker IS NOT NULL',(run_id,))]
        total=sum(counts.values())
        return {'runId':run_id,'total':total,'states':counts,'retiredCount':retired,'verifiedLinkCount':links[0],'verifiedPidCount':links[1],'reusePidCount':reuse,
                'pendingCount':sum(counts.get(k,0) for k in ('pending','reading','missing','prepared','submitted','unknown')),
                'incompleteCount':counts.get('read_incomplete',0),'errors':errors,'blockers':blockers}

def new_offer(listing,pid,campaign_id,source='selected',total=None,public=None,policy=None):
    """Fresh commercial facts for one selected/campaign plan, computed with the confirmed policy."""
    from lib.catalog_links import new_commission
    total=total if total is not None else listing.get('commission_rate')
    public=public if public is not None else listing.get('open_collab_rate')
    q=new_commission(total,public,policy)
    stats={'totalRaw':q['totalRaw'],'publicRaw':q['publicRaw'],'creatorRaw':q['creatorRaw'],'agencyRaw':q['agencyRaw']}
    def as_percent(raw):return format(Decimal(str(raw))/100,'f')
    value={'schema':'bdhub.catalog-offer.v1','pid':str(pid),'campaignId':str(campaign_id),'catalogSource':source,
            'title':str(listing.get('title') or pid)[:500],'stats':stats,
            'creatorPercent':as_percent(q['creatorRaw']),'totalPercent':as_percent(q['totalRaw']),'publicPercent':as_percent(q['publicRaw']),
            'agencyPercent':as_percent(q['agencyRaw']),'managementType':listing.get('managementType'),'managementEvidenceRef':listing.get('managementEvidenceRef'),
            'listing':{k:listing.get(k) for k in ('product_id','title','commission_rate','open_collab_rate','sales','product_rating','product_status','is_under_governed','unavailable_type','stock','campaign_id','fs_is_selected','managementType','managementEvidenceRef') if k in listing}}
    # One flat plan object is the single lookup path for link rules, creation and consumers.
    return value

def card_facts(*,list_id,campaign_id,wire,product,member,listing,policy):
    """Exact observed facts of one existing product list, before the reuse rule is applied."""
    raw=member.get('creator_commission_percent')
    creator=percent_raw(raw)
    if creator is None:raise ValueError('link_creator_rate_missing')
    stock=member.get('stock')
    return {'listId':str(list_id),'wireCampaignId':wire,'campaignId':str(campaign_id),'creatorRaw':str(creator),
            'publicRaw':str(percent_raw(member.get('plan_commission_percent'))) if member.get('plan_commission_percent') is not None else None,
            'totalRaw':str(percent_raw(listing.get('commission_rate'))) if listing.get('commission_rate') is not None else None,
            'platformValid':member.get('product_status') is not None and str(member.get('product_status'))=='2',
            'productEligible':member.get('is_under_governed') is not True and unavailable_allowed(member.get('unavailable_type'),member),
            'stock':str(stock) if stock is not None else None,'previouslyUsed':False,
            'listName':str(product.get('product_list_name') or '')}

def search_cards(read,pid):
    """Complete IM card search by PID. Incomplete pagination is never treated as absence."""
    seen={};total=None
    for page in range(1,6):
        body,sha=read(CARD,{'cur_page':page,'page_size':20,'version':1,'search_type':2,'key_word':str(pid)})
        data=body.get('data');n=data.get('total') if isinstance(data,dict) else None
        rows=data.get('list') if isinstance(data,dict) else None
        if type(n) is not int or n<0 or not isinstance(rows,list):raise ValueError('card_search_incomplete')
        if total is not None and total!=n:raise ValueError('card_search_incomplete')
        total=n
        for row in rows:
            lid=str(row.get('product_list_id') or '')
            if not lid.isdigit() or lid in seen:raise ValueError('card_search_duplicate')
            seen[lid]=(sha,row)
        if len(seen)>=total:break
        if not rows:raise ValueError('card_search_incomplete')
    else:raise ValueError('card_search_incomplete')
    return total,list(seen.items())

def classify_pid(pid,offer,read,policy):
    """Read one PID's existing links and decide reuse / review / missing for one offer.

    Only a complete search with no card at all may become `missing`.
    """
    wanted=str(offer['campaignId']);wire='0' if offer['catalogSource']=='selected' else wanted
    total,cards=search_cards(read,pid)
    facts=[];rates=[];matched=[]
    for lid,(sha,row) in cards:
        if str(row.get('campaign_id') or '0')!=wire:continue
        products=[p for p in (row.get('campaign_products') or []) if str(p.get('product_id'))==str(pid)]
        if len(products)!=1:continue
        matched.append((lid,sha,row,products[0]))
        raw=Decimal(str(products[0]['creator_commission_percent']))/100 if products[0].get('creator_commission_percent') is not None else None
        if raw is not None:rates.append(format(raw,'f'))
    if not matched:
        if total==0:return {'state':'missing','total':0,'rates':[],'reuse':[],'listing':offer['listing']}
        # Cards exist for this PID but none carries this plan's campaign binding: never a new link.
        return {'state':'review','total':total,'rates':sorted(set(rates)),'reuse':[],'blocker':'existing_links_other_campaign','listing':offer['listing']}
    best=None;observed=[]
    for lid,sha,row,product in matched:
        body,members_sha=read(MEMBERS,{'list_id':lid,'source':2 if wire=='0' else 1})
        data=body.get('data');rows=data.get('campaign_products') if isinstance(data,dict) else None
        if not isinstance(rows,list) or data.get('total_num')!=len(rows):raise ValueError('card_members_incomplete')
        member=next((p for p in rows if str(p.get('product_id'))==str(pid) and (str(p.get('campaign_id'))==wanted or wire!='0' and p.get('campaign_id') is None)),None)
        if member is None:
            observed.append({'listId':lid,'reusable':False,'reason':'link_member_binding_missing'});continue
        # This queue only prepares the official full-managed source, so the quantity gate is cancelled
        # here; a normal-catalog consumer keeps its own stock rule through the plan's own facts.
        merged=offer|{'managementType':offer.get('managementType') or 'full_managed','managementEvidenceRef':offer.get('managementEvidenceRef') or 'catalog-prepare:full_managed_source'}
        if require_stock(merged) and (member.get('stock') is None or Decimal(str(member.get('stock')))<=100):
            observed.append({'listId':lid,'reusable':False,'reason':'link_stock_below_rule'});continue
        try:fact=card_facts(list_id=lid,campaign_id=wanted,wire=wire,product=row,member=member,listing=offer['listing'],policy=policy)
        except ValueError:
            observed.append({'listId':lid,'reusable':False,'reason':'link_creator_rate_missing'});continue
        fact.update(memberEvidenceRefs=[sha,members_sha],publicRaw=fact['publicRaw'] or offer['publicPercent'],
                    productEligible=fact['productEligible'] and unavailable_allowed(product.get('unavailable_type'),merged))
        ok,reason=assess_existing(fact,policy)
        observed.append(fact|{'reusable':ok,'reason':reason})
        if ok and (best is None or Decimal(fact['creatorRaw'])>Decimal(best['creatorRaw'])):best=fact
    if best:return {'state':'reuse','total':total,'rates':sorted(set(rates)),'reuse':observed,'card':best,'listing':offer['listing']}
    return {'state':'review','total':total,'rates':sorted(set(rates)),'reuse':observed,'blocker':'existing_links_require_review','listing':offer['listing']}

def scan_lists(read,source='2',campaign_id='0',page_size=100,max_pages=500):
    """Every TapLink of one account route. Incomplete pagination is never treated as absence."""
    rows=[];seen=set();total=None
    for page in range(1,max_pages+1):
        body,sha=read(LISTS,{'campaign_id':str(campaign_id),'source':str(source),'cur_page':page,'page_size':page_size})
        data=body.get('data')
        if not isinstance(data,dict) or type(data.get('total')) is not int or data['total']<0:raise ValueError('taplink_inventory_malformed')
        if total is not None and total!=data['total']:raise ValueError('taplink_inventory_changed')
        total=data['total']
        batch=data.get('lists')
        if not isinstance(batch,list):raise ValueError('taplink_inventory_malformed')
        for raw in batch:
            lid=str(raw.get('id') or '')
            if not lid.isdigit() or lid in seen:raise ValueError('taplink_inventory_duplicate')
            seen.add(lid)
            rows.append({'list_id':lid,'name':str(raw.get('name') or ''),'url':str(raw.get('url') or ''),
                         'product_total':raw.get('total'),'platform_updated_at':raw.get('update_time')})
        if len(rows)==total:return total,rows
        if not batch or len(batch)<page_size or len(rows)>total:raise ValueError('taplink_inventory_incomplete')
    raise ValueError('taplink_inventory_limit')

def read_members(read,list_id,source='2'):
    """Every member of one TapLink, cursor-paginated. An incomplete read is never absence."""
    rows=[];seen=set();cursor=None;total=None
    for _ in range(101):
        params={'list_id':str(list_id),'source':str(source)}
        if cursor is not None:params['cursor']=cursor
        body,sha=read(MEMBERS,params)
        data=body.get('data')
        if not isinstance(data,dict) or type(data.get('total_num')) is not int or data['total_num']<0:raise ValueError('taplink_members_malformed')
        if total is not None and total!=data['total_num']:raise ValueError('taplink_members_changed')
        total=data['total_num']
        batch=data.get('campaign_products')
        if not isinstance(batch,list):raise ValueError('taplink_members_malformed')
        for m in batch:
            pid=str(m.get('product_id') or '')
            if not pid.isdigit() or (list_id,pid) in seen:raise ValueError('taplink_members_incomplete')
            seen.add((list_id,pid));rows.append(m)
        if len(rows)==total:return rows
        nxt=data.get('next_cursor')
        if not batch or cursor==nxt:raise ValueError('taplink_members_incomplete')
        cursor=nxt
    raise ValueError('taplink_members_limit')

def member_facts(row,member,policy,listing=None):
    """Observed facts of one inventory member, before the reuse rule is applied.

    Commission/status come from the member; the total rate comes from the product plan,
    because the card member does not carry it.
    """
    creator=percent_raw(member.get('creator_commission_percent'))
    public=percent_raw(member.get('plan_commission_percent'))
    total=percent_raw((listing or {}).get('commission_rate'))
    return {'listId':str(row['list_id']),'listName':str(row.get('name') or ''),'pid':str(member.get('product_id') or ''),
            'campaignId':str(member.get('campaign_id') or ''),'creatorRaw':str(creator) if creator is not None else None,
            'publicRaw':str(public) if public is not None else None,'totalRaw':str(total) if total is not None else None,
            'platformValid':member.get('product_status') is not None and str(member.get('product_status'))=='2',
            'productEligible':member.get('is_under_governed') is not True and unavailable_allowed(member.get('unavailable_type'),member),
            'stock':str(member.get('stock')) if member.get('stock') is not None else None,'previouslyUsed':False}

class TaplinkInventory:
    """Account-wide TapLink inventory: list once, read members once, reuse many times."""
    def __init__(self,root):
        self.root=Path(root)
        self.db=sqlite3.connect(self.root/'var/catalog-links.sqlite',timeout=15);self.db.row_factory=sqlite3.Row
        self.db.executescript(SCHEMA)
    def close(self):self.db.close()
    def save_list(self,row,source='2',campaign_id='0',now=None):
        now=now if now is not None else time.time()
        with self.db:
            # Upsert must not clear members_at, otherwise a re-scan loses the resume marker.
            self.db.execute('''INSERT INTO catalog_tap_list(list_id,source,campaign_id,name,url,product_total,platform_updated_at,observed,members_at)
VALUES(?,?,?,?,?,?,?,?,NULL)
ON CONFLICT(list_id) DO UPDATE SET source=excluded.source,campaign_id=excluded.campaign_id,name=excluded.name,
url=excluded.url,product_total=excluded.product_total,platform_updated_at=excluded.platform_updated_at,observed=excluded.observed''',
                (str(row['list_id']),str(source),str(campaign_id),row.get('name'),row.get('url'),row.get('product_total'),row.get('platform_updated_at'),now))
    def save_members(self,list_id,list_name,members,now=None):
        now=now if now is not None else time.time()
        with self.db:
            self.db.execute('DELETE FROM catalog_tap_member WHERE list_id=?',(str(list_id),))
            for m in members:
                self.db.execute('INSERT OR REPLACE INTO catalog_tap_member VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (str(list_id),str(m.get('product_id') or ''),str(m.get('campaign_id') or '') or None,
                     str(percent_raw(m.get('creator_commission_percent'))) if m.get('creator_commission_percent') is not None else None,
                     str(percent_raw(m.get('plan_commission_percent'))) if m.get('plan_commission_percent') is not None else None,
                     str(m.get('product_status')) if m.get('product_status') is not None else None,
                     1 if m.get('is_under_governed') is True else 0,
                     str(m.get('unavailable_type')) if m.get('unavailable_type') is not None else None,
                     str(m.get('stock')) if m.get('stock') is not None else None,
                     str(list_name or ''),now))
            self.db.execute('UPDATE catalog_tap_list SET members_at=? WHERE list_id=?',(now,str(list_id)))
    def lists_pending_members(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM catalog_tap_list WHERE members_at IS NULL ORDER BY list_id')]
    def lists(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM catalog_tap_list ORDER BY list_id')]
    def members_for_pid(self,pid):
        return [dict(r) for r in self.db.execute('SELECT * FROM catalog_tap_member WHERE pid=? ORDER BY list_id',(str(pid),))]
    def summary(self):
        lists=self.db.execute('SELECT count(*),coalesce(sum(product_total),0),coalesce(sum(members_at IS NOT NULL),0) FROM catalog_tap_list').fetchone()
        members=self.db.execute('SELECT count(*),count(DISTINCT pid) FROM catalog_tap_member').fetchone()
        return {'lists':lists[0],'declaredProducts':lists[1],'membersRead':lists[2],'members':members[0],'coveredPids':members[1],
                'observedAt':self.db.execute('SELECT max(observed) FROM catalog_tap_list').fetchone()[0]}

def reconcile_from_inventory(prep,inv,items,offers,now=None):
    """Judge each queued plan against the cached inventory instead of one search per PID.

    Candidate cards and member facts come from the account-wide inventory; only the
    product plan (listing) still needs a live read. Rules are unchanged: the same
    assess_existing call decides reuse.
    """
    now=now if now is not None else time.time()
    policy=prep.policy
    states={}
    for it in items:
        pid=str(it['pid']);cid=str(it['campaign_id']);src=it['catalog_source'];run_id=it['run_id']
        offer=offers.get(pid)
        if not offer or str(offer['campaignId'])!=cid:
            code='selected_campaign_changed' if offer else 'listing_read_unresolved'
            with prep.db:
                prep.db.execute("UPDATE catalog_prepare_item SET state='read_incomplete',error=?,lease_until=0,updated=? WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=?",(code,now,run_id,pid,cid,src))
            states['read_incomplete']=states.get('read_incomplete',0)+1;continue
        stats=offer.get('stats') or {};total=stats.get('totalRaw');public_plan=stats.get('publicRaw')
        merged={'managementType':offer.get('managementType') or 'full_managed','managementEvidenceRef':offer.get('managementEvidenceRef') or 'catalog-prepare:full_managed_source'}
        observed=[];best=None
        for m in inv.members_for_pid(pid):
            if str(m.get('member_campaign_id') or '')!=cid:continue
            fact={'listId':str(m['list_id']),'listName':str(m.get('list_name') or ''),'pid':pid,'campaignId':cid,
                  'creatorRaw':m.get('creator_percent'),'publicRaw':m.get('public_percent') or public_plan,'totalRaw':total,
                  'platformValid':str(m.get('product_status'))=='2',
                  'productEligible':not m.get('governed') and unavailable_allowed(m.get('unavailable_type'),merged),
                  'stock':m.get('stock'),'previouslyUsed':False}
            ok,reason=assess_existing(fact,policy)
            observed.append(fact|{'reusable':ok,'reason':reason})
            if ok and (best is None or (fact['creatorRaw'] and Decimal(fact['creatorRaw'])>Decimal(best['creatorRaw']))):best=fact
        state='reuse' if best else ('review' if observed else 'missing')
        plan_listing={'product_id':pid,'title':offer.get('title'),'creatorPercent':offer.get('creatorPercent'),
                      'publicPercent':offer.get('publicPercent'),'totalPercent':offer.get('totalPercent'),
                      'agencyPercent':offer.get('agencyPercent'),'managementType':offer.get('managementType'),
                      'managementEvidenceRef':offer.get('managementEvidenceRef')}
        with prep.db:
            prep.db.execute("UPDATE catalog_prepare_item SET state=?,attempts=attempts+1,lease_until=0,error=NULL,blocker=?,title=?,creator_percent=?,public_percent=?,total_percent=?,listing=?,read_at=?,updated=? WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=?",
                (state,'existing_links_require_review' if state=='review' else None,str(offer.get('title') or '')[:500],
                 offer.get('creatorPercent'),offer.get('publicPercent'),offer.get('totalPercent'),encoded(plan_listing),now,now,run_id,pid,cid,src))
            prep.db.execute('DELETE FROM catalog_prepare_reuse WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=?',(run_id,pid,cid,src))
            for c in observed:
                prep.db.execute('INSERT OR REPLACE INTO catalog_prepare_reuse VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (run_id,pid,cid,src,c['listId'],c['creatorRaw'],c['publicRaw'],1 if c['reusable'] else 0,c['reason'],now))
        states[state]=states.get(state,0)+1
    return states
