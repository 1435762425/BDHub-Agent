"""Live sending metrics from persisted attempts and confirmed delivery states."""
import statistics

def speed_status(store,plan):
 db=store.db;now=store.clock()
 if not db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery_part'").fetchone():return None
 rows=db.execute("SELECT d.id,d.creator_id,d.created,p.kind,p.started,p.state FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id WHERE d.plan_id=? AND p.started>?",(plan,now-3600)).fetchall()
 def confirmed(seconds,kind=None):return [r for r in rows if r['state']=='confirmed' and r['started']>=now-seconds and (kind is None or r['kind']==kind)]
 buckets=[]
 for i in range(14,-1,-1):
  start=int(now//60)*60-i*60
  buckets.append({'at':start,'contacts':sum(r['kind']=='card' and r['state']=='confirmed' and start<=r['started']<start+60 for r in rows),'messages':sum(r['state']=='confirmed' and start<=r['started']<start+60 for r in rows)})
 policy=None
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_speed_policy'").fetchone():
  row=db.execute('SELECT * FROM cycle_speed_policy WHERE plan_id=? ORDER BY at DESC LIMIT 1',(plan,)).fetchone();policy=dict(row) if row else None
 groups={}
 for r in rows:groups.setdefault(r['id'],{})[r['kind']]=r
 intervals=[g['text']['started']-g['card']['started'] for g in groups.values() if 'text' in g and 'card' in g and g['text']['state']=='confirmed' and g['card']['state']=='confirmed']
 optimized=[g['text']['started']-g['card']['started'] for g in groups.values() if policy and 'text' in g and 'card' in g and g['text']['state']=='confirmed' and g['card']['started']>=policy['at']]
 waits=[r['started']-r['created'] for r in rows if r['kind']=='card' and r['state']=='confirmed']
 stage=[]
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_bulk_timing'").fetchone():
  stage=[dict(r) for r in db.execute('SELECT stage,round(avg(CASE WHEN exit_code=0 THEN seconds END),2) averageSeconds,count(*) samples,sum(CASE WHEN exit_code<>0 THEN 1 ELSE 0 END) failedOperations FROM cycle_bulk_timing WHERE at>? GROUP BY stage',(now-900,))]
 runtime=None
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_bulk_runtime'").fetchone():
  r=db.execute('SELECT * FROM cycle_bulk_runtime ORDER BY seen DESC LIMIT 1').fetchone()
  if r:runtime={**dict(r),'fresh':now-r['seen']<90}
 native_rejections=db.execute("SELECT count(*) FROM cycle_platform_signal s JOIN cycle_delivery d ON d.id=s.delivery_id WHERE d.plan_id=? AND s.at>? AND s.outcome='rejected'",(plan,now-900)).fetchone()[0] if db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_platform_signal'").fetchone() else 0
 attempts=[r for r in rows if r['started']>=now-900]
 return {'policy':policy,'optimizedSamples':len(optimized),'optimizedCardToTextMedianSeconds':round(statistics.median(optimized),2) if optimized else None,'observedAt':now,'contactsPerMinute':round(len(confirmed(300,'card'))/5,2),'messagesPerMinute':round(len(confirmed(300))/5,2),
 'lastMinuteContacts':len(confirmed(60,'card')),'last15MinutesContacts':len(confirmed(900,'card')),
 'cardToTextMedianSeconds':round(statistics.median(intervals),2) if intervals else None,
 'preparedToCardMedianSeconds':round(statistics.median(waits),2) if waits else None,
 'unconfirmedAttemptPercent':round(100*sum(r['state']!='confirmed' for r in attempts)/len(attempts),1) if attempts else None,
 'buckets':buckets,'stages':stage,'worker':runtime,'nativeRejections':native_rejections,'basis':'confirmed dispatch timestamps; rolling five-minute rate; unconfirmed includes in-flight and unknown, not all are failures'}
