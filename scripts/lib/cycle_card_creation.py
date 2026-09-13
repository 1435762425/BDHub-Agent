"""One-shot card creation ledger. Transport supplied explicitly by the caller."""
import json,time,re
from lib.second_cycle import CycleError,digest,encoded,assess_offer
SCHEMA='''CREATE TABLE IF NOT EXISTS cycle_card_creation(
 id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,pid TEXT NOT NULL,offer_key TEXT NOT NULL,offer_json TEXT NOT NULL,
 plan_revision INTEGER NOT NULL,list_name TEXT NOT NULL,state TEXT NOT NULL,receipt TEXT,readback TEXT,created_at REAL NOT NULL);
 CREATE UNIQUE INDEX IF NOT EXISTS cycle_one_open_card ON cycle_card_creation(plan_id,pid) WHERE state IN ('prepared','started','response_saved','unknown');'''
class CardCreation:
 def __init__(self,store):self.store=store;store.db.executescript(SCHEMA)
 def get(self,i):
  r=self.store.db.execute('SELECT * FROM cycle_card_creation WHERE id=?',(i,)).fetchone()
  if not r:raise CycleError('creation_missing')
  return dict(r)
 def prepare(self,plan,offer,short_name):
  if not assess_offer(offer,self.store.clock())['eligible']:raise CycleError('offer_not_eligible')
  if offer.get('catalogSource') not in ('selected','campaign'):raise CycleError('creation_route_not_enabled')
  identifier='card-create-'+digest([plan,offer])[:32]
  with self.store.tx():
   prior=self.store.db.execute('SELECT * FROM cycle_card_creation WHERE id=?',(identifier,)).fetchone()
   if prior:return dict(prior)
   other=self.store.db.execute("SELECT id FROM cycle_card_creation WHERE plan_id=? AND pid=? AND state IN ('prepared','started','response_saved','unknown')",(plan,offer['pid'])).fetchone()
   if other:raise CycleError('prior_creation_unresolved')
   p=self.store._plan(plan)
   if p['state']!='active':raise CycleError('plan_paused')
   suffix=' '+offer['creatorPercent']+'% '+identifier[-6:]
   short=short_name
   while len('BJN '+short+suffix)>50:short=short.rsplit(' ',1)[0] if ' ' in short else short[:-1]
   name='BJN '+short+suffix
   self.store.db.execute('INSERT INTO cycle_card_creation VALUES(?,?,?,?,?,?,?,\'prepared\',NULL,NULL,?)',(identifier,plan,offer['pid'],offer['offerKey'],encoded(offer),p['revision'],name,self.store.clock()))
  return self.get(identifier)
 def invalidate_preflight(self,i,fresh):
  """Retire only an unsubmitted intent after exact current commercial evidence."""
  with self.store.tx():
   row=self.get(i);offer=json.loads(row['offer_json'])
   if row['state']!='prepared' or row['receipt'] is not None:raise CycleError('cannot_invalidate_attempted_creation')
   if any(fresh.get(k)!=offer.get(k) for k in ('pid','offerKey','campaignId','catalogSource')):raise CycleError('preflight_identity_mismatch')
   if not fresh.get('evidenceRef') or not isinstance(fresh.get('observedAt'),(int,float)) or not 0<=self.store.clock()-fresh['observedAt']<=60:raise CycleError('preflight_evidence_missing')
   assessment=assess_offer(fresh,self.store.clock())
   if assessment['eligible'] and fresh.get('creatorPercent')==offer['creatorPercent']:raise CycleError('preflight_still_eligible')
   evidence={'reason':'current_offer_changed','freshOffer':fresh,'assessment':assessment,'invalidatedAt':self.store.clock(),'platformCreateAttempts':0}
   self.store.db.execute("UPDATE cycle_card_creation SET state='invalidated',readback=? WHERE id=?",(encoded(evidence),i))
  return self.get(i)
 def begin(self,i):
  with self.store.tx():
   row=self.get(i);p=self.store._plan(row['plan_id'])
   if row['state']!='prepared':raise CycleError('creation_already_attempted')
   if p['state']!='active' or p['revision']!=row['plan_revision']:raise CycleError('plan_changed')
   if self.store.db.execute("SELECT 1 FROM cycle_card_creation WHERE plan_id=? AND id<>? AND state IN ('started','response_saved','unknown')",(row['plan_id'],i)).fetchone():raise CycleError('another_creation_unknown')
   self.store.db.execute("UPDATE cycle_card_creation SET state='started' WHERE id=?",(i,))
 def save_receipt(self,i,receipt):
  with self.store.tx():
   if self.get(i)['state']!='started':raise CycleError('creation_receipt_state')
   self.store.db.execute("UPDATE cycle_card_creation SET state='response_saved',receipt=? WHERE id=?",(encoded(receipt),i))
 def unknown(self,i):
  with self.store.tx():self.store.db.execute("UPDATE cycle_card_creation SET state='unknown' WHERE id=? AND state IN ('started','response_saved')",(i,))
 def confirm(self,i,card,reused=False):
  with self.store.tx():
   row=self.get(i);offer=json.loads(row['offer_json'])
   if row['state']=='verified':return
   if card.get('state')!='verified_read_only' or card.get('pid')!=offer['pid'] or card.get('sourceCampaignId')!=offer['campaignId'] or card.get('creatorPercent')!=offer['creatorPercent']:raise CycleError('card_binding_mismatch')
   if card.get('wireCampaignId')!=('0' if offer['catalogSource']=='selected' else offer['campaignId']) or not re.fullmatch(r'[0-9]{1,32}',str(card.get('listId',''))):raise CycleError('card_binding_mismatch')
   if not reused:
    receipt=json.loads(row['receipt']) if row['receipt'] else {}
    if receipt.get('list_id'):
     if receipt['list_id']!=card['listId']:raise CycleError('card_receipt_mismatch')
    elif card.get('verifiedListName')!=row['list_name']:raise CycleError('card_name_not_verified')
   if not reused and row['state'] not in ('started','response_saved','unknown'):raise CycleError('creation_not_started')
   if reused and row['state']!='prepared':raise CycleError('reuse_not_prepared')
   self.store.db.execute("UPDATE cycle_card_creation SET state='verified',readback=? WHERE id=?",(encoded({**card,'reused':reused}),i))
   self.store.db.execute('INSERT OR REPLACE INTO cycle_card_check VALUES(?,?,?,?)',(row['plan_id'],offer['offerKey'],digest(offer),encoded(card)))
