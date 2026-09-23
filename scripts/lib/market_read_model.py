"""Durable, bounded market page snapshots published after local state changes."""
from __future__ import annotations

import json
import time

from lib.second_cycle import CycleError,digest,encoded

VIEWS=frozenset({'operations','catalog'})

def _required(store):
 tables={row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 if not {'market_read_generation','market_read_head'}<=tables:raise CycleError('market_read_model_migration_required')

def publish(store,market,view,payload,*,observed_at=None):
 _required(store)
 if view not in VIEWS or not isinstance(market,str) or not isinstance(payload,dict):raise CycleError('market_read_model_invalid')
 if payload.get('market')!=market:raise CycleError('market_read_model_scope_mismatch')
 observed=float(store.clock() if observed_at is None else observed_at);fingerprint=digest(payload)
 with store.tx():
  head=store.db.execute('SELECT * FROM market_read_head WHERE market=? AND view=?',(market,view)).fetchone()
  if head:
   prior=store.db.execute('SELECT source_fingerprint,payload_json FROM market_read_generation WHERE generation_id=?',(head['generation_id'],)).fetchone()
   if prior and prior['source_fingerprint']==fingerprint:
    store.db.execute('UPDATE market_read_head SET observed_at=?,updated_at=? WHERE market=? AND view=?',(observed,store.clock(),market,view))
    return {'market':market,'view':view,'generationId':head['generation_id'],'revision':head['revision'],'observedAt':observed,'changed':False}
  revision=(head['revision'] if head else 0)+1
  generation='read-'+digest([market,view,revision,fingerprint,observed])[:28]
  public={**payload,'observationTime':observed,'readModelRevision':revision}
  store.db.execute('INSERT INTO market_read_generation VALUES(?,?,?,?,?,?,?,?)',(generation,market,view,revision,fingerprint,encoded(public),observed,store.clock()))
  store.db.execute('''INSERT INTO market_read_head VALUES(?,?,?,?,?,?)
   ON CONFLICT(market,view) DO UPDATE SET generation_id=excluded.generation_id,
    revision=excluded.revision,observed_at=excluded.observed_at,updated_at=excluded.updated_at''',(market,view,generation,revision,observed,store.clock()))
 return {'market':market,'view':view,'generationId':generation,'revision':revision,'observedAt':observed,'changed':True}

def read(store,market,view,*,max_age=None,now=None):
 _required(store)
 if view not in VIEWS:raise CycleError('market_read_model_invalid')
 row=store.db.execute('''SELECT h.*,g.payload_json FROM market_read_head h JOIN market_read_generation g
  ON g.generation_id=h.generation_id WHERE h.market=? AND h.view=?''',(market,view)).fetchone()
 if not row:return None
 stamp=float(time.time() if now is None else now)
 if max_age is not None and stamp-float(row['observed_at'])>max_age:return None
 value=json.loads(row['payload_json'])
 if not isinstance(value,dict) or value.get('market')!=market:raise CycleError('market_read_model_scope_mismatch')
 return value
