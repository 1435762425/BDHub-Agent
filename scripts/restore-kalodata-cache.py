#!/usr/bin/env python3
"""Retain existing queried pages via the same supported cached API; no eligibility changes."""
import argparse,json,sqlite3,sys,time
from pathlib import Path
from contextlib import closing
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.batch_source_runtime import kalodata_provider
from lib.cycle_kalodata import PATH,parse_page
from lib.second_cycle import encoded,digest

def main():
 p=argparse.ArgumentParser();p.add_argument('--limit',type=int,default=10);a=p.parse_args()
 if not 1<=a.limit<=100:p.error('limit 1..100')
 cache=ROOT/'var/kalodata-source-cache.sqlite'
 with closing(sqlite3.connect(cache)) as saved,closing(sqlite3.connect((ROOT/'var/batch-tasks.sqlite').as_uri()+'?mode=ro',uri=True)) as source:
  cache.chmod(0o600);source.row_factory=sqlite3.Row
  saved.execute('CREATE TABLE IF NOT EXISTS cached_page(job_id TEXT,cursor TEXT,fingerprint TEXT,payload TEXT,observed REAL,PRIMARY KEY(job_id,cursor,fingerprint))');saved.commit()
  existing={(r[0],r[1]) for r in saved.execute('SELECT job_id,cursor FROM cached_page')}
  rows=source.execute("SELECT j.id,j.pid,j.offer,p.cursor,sp.window_start,sp.window_end FROM batch_source_page p JOIN batch_source_job j ON j.id=p.job_id JOIN batch_source_plan sp ON sp.task_id=j.task_id JOIN batch_task t ON t.id=j.task_id WHERE json_extract(t.spec,'$.market')='it' AND json_extract(p.payload,'$.skipped.no_sales')>0 ORDER BY p.rowid").fetchall()
  selected=[r for r in rows if (r['id'],r['cursor']) not in existing][:a.limit]
  result={'pages':0,'rows':0,'zeroSalesRows':0,'sourceEligibilityChanged':False,'platformWrites':0}
  with kalodata_provider(ROOT) as provider:
   for row in selected:
    body=provider.request(PATH,{'startDate':row['window_start'],'endDate':row['window_end'],'authority':True,'pageSize':50,'pageNo':int(row['cursor'] or '1'),'sort':[{'field':'revenue','type':'DESC'}],'id':row['pid']})
    claim=dict(row)|{'offer_key':json.loads(row['offer'])['offerKey']};page=parse_page(body,claim,time.time())
    payload={'claim':claim,'cached':body.get('cached'),'sourceRows':page['sourceRows'],'rowsFingerprint':page['rowsFingerprint']}
    saved.execute('INSERT OR IGNORE INTO cached_page VALUES(?,?,?,?,?)',(row['id'],row['cursor'],digest(payload),encoded(payload),time.time()));saved.commit()
    result['pages']+=1;result['rows']+=len(page['sourceRows']);result['zeroSalesRows']+=page['skipped'].get('no_sales',0)
    if body.get('cached') is not True:result['stopped']='response_not_marked_cached';break
  print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
