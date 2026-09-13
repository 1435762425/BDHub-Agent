#!/usr/bin/env python3
"""Read fixed-target readiness from the local Italy store; no external requests."""
import json,sqlite3,sys
from pathlib import Path
from contextlib import closing
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleStore
from lib.batch_preparation import inspect_preparation

def main():
    data=json.loads(sys.stdin.read(4097))
    if not isinstance(data,dict) or set(data)!={'target'}:raise ValueError('invalid_input')
    with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=True) as store,closing(sqlite3.connect((ROOT/'var/creator-identities.sqlite').as_uri()+'?mode=ro',uri=True)) as ids:
        row=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()
        if not row:raise ValueError('scope_missing')
        def person(creator,oec):return ids.execute("SELECT 1 FROM creator_identity WHERE market='it' AND creator_id=? AND oec_id=? AND handle_conflict=0",(creator,oec)).fetchone() is not None
        result=inspect_preparation(store,row['id'],person,data['target'])
        source_path=ROOT/'var/global-source.sqlite'
        if source_path.exists():
            from lib.global_source import GlobalSources
            source=GlobalSources(source_path,readonly=True)
            try:
                status=source.status();result['globalSource']={k:status.get(k) for k in ('available','state','products','reportedTotal','published','detailProducts','listedUnselectedProducts','activePublished')}
                if status.get('published') or status.get('activePublished'):
                    result['blockers']=[x for x in result['blockers'] if x!='global_opportunity_source_not_accepted']+['global_selection_and_task_preparation_pending']
            finally:source.close()
        print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':
    try:main()
    except Exception as e:print(json.dumps({'error':'preparation_unavailable'}));sys.exit(1)
