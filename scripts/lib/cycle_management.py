"""Project official global-only PID evidence into current cycle product policy."""
import json,sqlite3
from contextlib import closing
from lib.second_cycle import CycleStore
from lib.global_source import FILTER,SOURCE

def sync_full_managed(cycle_path,source_path,expected_scope):
    with closing(sqlite3.connect(source_path.resolve().as_uri()+'?mode=ro',uri=True)) as source,CycleStore(cycle_path) as cycle:
        source.row_factory=sqlite3.Row
        p=cycle.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=?",(expected_scope['market'],)).fetchone()
        if not p:raise ValueError('management_plan_missing')
        count=0
        for head in source.execute("SELECT r.* FROM global_source_head h JOIN global_source_run r ON r.id=h.run_id WHERE r.state='completed' AND r.identity_unchanged=1"):
            scope=json.loads(head['scope'])
            if any(scope.get(k)!=v for k,v in expected_scope.items()):continue
            if scope.get('source')!=SOURCE or scope.get('filter')!=FILTER:raise ValueError('management_source_mismatch')
            rows=source.execute('SELECT pid,fingerprint FROM global_source_product WHERE run_id=?',(head['id'],)).fetchall()
            with cycle.tx():
                for row in rows:
                    ref='global-source:'+head['id']+':'+row['pid']+':'+row['fingerprint']
                    cycle.db.execute("INSERT INTO cycle_product_management VALUES(?,?,'full_managed',?,?) ON CONFLICT(plan_id,pid) DO UPDATE SET kind=excluded.kind,evidence_ref=excluded.evidence_ref,observed=excluded.observed WHERE excluded.observed>=cycle_product_management.observed",(p[0],row['pid'],ref,head['updated']))
            count+=len(rows)
        return {'fullManagedPids':count,'platformWrites':0,'oldDatabaseWrites':0}
