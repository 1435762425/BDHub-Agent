"""Persistent cadence for read-only supply stages; never invokes sending or creation."""
import json
from lib.second_cycle import CycleError,encoded
SCHEMA='''CREATE TABLE IF NOT EXISTS cycle_schedule(plan_id TEXT NOT NULL,stage TEXT NOT NULL,period INTEGER NOT NULL,due REAL NOT NULL,state TEXT NOT NULL DEFAULT 'idle',run_id TEXT,started REAL,finished REAL,failures INTEGER NOT NULL DEFAULT 0,result TEXT,PRIMARY KEY(plan_id,stage));'''
PERIODS={'catalog_selected':86400,'catalog_campaign':86400,'kalodata':30,'identity_reconcile':60,'materials_check':15}
ACTIVE_STAGES=tuple(PERIODS)

def _active_clause(column='stage'):
 return f"{column} IN ({','.join('?' for _ in ACTIVE_STAGES)})"

class Scheduler:
 def __init__(self,store):self.s=store;store.db.executescript(SCHEMA)
 def initialize(self,plan):
  with self.s.tx():
   for stage,period in PERIODS.items():self.s.db.execute('INSERT OR IGNORE INTO cycle_schedule(plan_id,stage,period,due) VALUES(?,?,?,?)',(plan,stage,period,self.s.clock()))
 def claim(self,plan,blocked_stages=()):
  with self.s.tx():
   if self.s._plan(plan)['state']!='active':return None
   if self.has_running(plan):return None
   clause=(' AND stage NOT IN ('+','.join('?' for _ in blocked_stages)+')') if blocked_stages else ''
   r=self.s.db.execute("SELECT * FROM cycle_schedule WHERE plan_id=? AND "+_active_clause()+" AND due<=? AND state<>'running'"+clause+" ORDER BY due,stage LIMIT 1",(plan,*ACTIVE_STAGES,self.s.clock(),*blocked_stages)).fetchone()
   if not r:return None
   run=f"{r['stage']}-{int(self.s.clock()*1000000)}"
   self.s.db.execute("UPDATE cycle_schedule SET state='running',run_id=?,started=? WHERE plan_id=? AND stage=?",(run,self.s.clock(),plan,r['stage']))
   return dict(r)|{'run_id':run}
 def complete(self,plan,stage,run_id,success,result):
  with self.s.tx():
   r=self.s.db.execute('SELECT * FROM cycle_schedule WHERE plan_id=? AND stage=?',(plan,stage)).fetchone()
   if not r or r['state']!='running' or r['run_id']!=run_id:raise CycleError('schedule_stale_run')
   busy=result.get('reason')=='account_busy'
   failures=0 if success or busy else r['failures']+1;delay=15 if busy else (45 if result.get('checkpointed') else r['period']) if success else min(3600,60*2**min(failures,6))
   self.s.db.execute('UPDATE cycle_schedule SET state=?,due=?,finished=?,failures=?,result=? WHERE plan_id=? AND stage=?',('idle' if success else 'waiting',self.s.clock()+delay,self.s.clock(),failures,encoded(result),plan,stage))
 def recover(self,plan):
  # Called only after acquiring the process lock; the old child must not survive.
  with self.s.tx():self.s.db.execute("UPDATE cycle_schedule SET state='waiting',due=?,result=? WHERE plan_id=? AND "+_active_clause()+" AND state='running'",(self.s.clock()+60,encoded({'reason':'reader_interrupted'}),plan,*ACTIVE_STAGES))
 def has_running(self,plan):
  return bool(self.s.db.execute("SELECT 1 FROM cycle_schedule WHERE plan_id=? AND "+_active_clause()+" AND state='running'",(plan,*ACTIVE_STAGES)).fetchone())

def schedule_status(store,plan):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_schedule'").fetchone():return []
 return [dict(r) for r in store.db.execute('SELECT stage,state,due,finished,failures,result FROM cycle_schedule WHERE plan_id=? AND '+_active_clause()+' ORDER BY stage',(plan,*ACTIVE_STAGES))]
