"""Project completed video evidence into the existing identity handoff source index."""
import json
from lib.second_cycle import encoded


def backfill_current(db,*,market=None,pid=None):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'plan','source_edge','source_edge_index','video_lead_current','kalodata_video_run'}<=tables:return 0
    rows=db.execute('''SELECT v.*,p.id AS plan_id,r.window_start,r.window_end FROM video_lead_current v
      JOIN kalodata_video_run r ON r.run_id=v.run_id
      JOIN plan p ON p.market=v.market AND p.institution='bjn-local-research'
      WHERE (? IS NULL OR v.market=?) AND (? IS NULL OR v.pid=?)''',(market,market,pid,pid)).fetchall()
    # Migration connections and runtime stores both use named rows.
    added=0
    for row in rows:
        sid=f"video:{row['run_id']}:{row['video_id']}"
        edge={'sourceId':sid,'pid':row['pid'],'sourceKind':'kalodata_video','sourceClass':'B',
              'sourceHandle':row['handle'],'sourceRank':1,'units':int(row['video_sale'] or 0),'creatorId':None,'oec':None,
              'offerKey':'video:'+row['pid'],'market':row['market'],
              'videoId':row['video_id'],'videoViews':row['views'],'videoReleasedAt':row['released_at'],
              'windowStart':row['window_start'],'windowEnd':row['window_end'],
              'evidenceRef':f"kalodata-video:{row['run_id']}:{row['video_id']}",
              'observedAt':row['observed_at'],'historicalOwnership':'unverified'}
        added+=db.execute('INSERT OR IGNORE INTO source_edge(plan_id,source_id,payload) VALUES(?,?,?)',
                          (row['plan_id'],sid,encoded(edge))).rowcount
        db.execute('''INSERT OR IGNORE INTO source_edge_index(plan_id,source_id,pid,source_handle,source_rank,units,
          window_start,window_end,source_kind,revenue_value,revenue_currency) VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL)''',
          (row['plan_id'],sid,row['pid'],row['handle'],1,int(row['video_sale'] or 0),row['window_start'],row['window_end'],'kalodata_video'))
    return added
