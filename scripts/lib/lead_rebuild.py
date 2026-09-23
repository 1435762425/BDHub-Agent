"""Reset only current unsent lead projections for an authorized from-scratch rebuild."""
from contextlib import closing
from pathlib import Path
import sqlite3

from lib.second_cycle import CycleError


def _count(db,table):return db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]

def _scoped_count(db):
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='leads_page_scope'").fetchone()
    return _count(db,'leads_page_scope') if exists else 0


def reset_current(root,*,confirmed=False):
    root=Path(root);leads=root/'var/kalodata-leads.sqlite';cycle=root/'var/second-cycle.sqlite'
    if not leads.exists() or not cycle.exists():raise CycleError('lead_rebuild_state_missing')
    with closing(sqlite3.connect(leads)) as ldb,closing(sqlite3.connect(cycle)) as cdb:
        before={'leadQueries':_count(ldb,'leads_query'),'leadPages':_count(ldb,'leads_page'),
                'scopedPages':_scoped_count(ldb),
                'leadAttempts':_count(ldb,'leads_attempt'),'currentHeads':_count(cdb,'lead_query_head'),
                'historicalRuns':_count(cdb,'lead_query_run'),'historicalSelections':_count(cdb,'lead_query_selection'),
                'sourceEdges':_count(cdb,'source_edge_index'),
                'sentPairs':cdb.execute("SELECT count(*) FROM (SELECT DISTINCT creator_id,pid FROM cycle_delivery "
                                        "WHERE state IN ('confirmed','partial_delivery'))").fetchone()[0]}
        if not confirmed:return {'mode':'preview','before':before,'platformWrites':0,'realSends':0}
        with ldb:
            ldb.execute('DELETE FROM leads_page');ldb.execute('DELETE FROM leads_query');ldb.execute('DELETE FROM leads_attempt')
            if _scoped_count(ldb):ldb.execute('DELETE FROM leads_page_scope')
        with cdb:cdb.execute('DELETE FROM lead_query_head')
        after={'leadQueries':_count(ldb,'leads_query'),'leadPages':_count(ldb,'leads_page'),
               'scopedPages':_scoped_count(ldb),
               'leadAttempts':_count(ldb,'leads_attempt'),'currentHeads':_count(cdb,'lead_query_head'),
               'historicalRuns':_count(cdb,'lead_query_run'),'historicalSelections':_count(cdb,'lead_query_selection'),
               'sourceEdges':_count(cdb,'source_edge_index'),
               'sentPairs':cdb.execute("SELECT count(*) FROM (SELECT DISTINCT creator_id,pid FROM cycle_delivery "
                                       "WHERE state IN ('confirmed','partial_delivery'))").fetchone()[0]}
    if after['sentPairs']!=before['sentPairs'] or after['historicalRuns']!=before['historicalRuns'] or \
            after['sourceEdges']!=before['sourceEdges']:
        raise CycleError('lead_rebuild_preservation_failed')
    return {'mode':'applied','before':before,'after':after,'platformWrites':0,'realSends':0}
