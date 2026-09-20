"""Publish one current, bounded Kalodata lead generation without deleting raw evidence."""
import json
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import time

from lib.second_cycle import CycleError, digest, encoded

POLICY_VERSION = "kalodata-positive-gmv-v2"
DEFAULT_LIMIT = 20


def _edge(value):
    if not isinstance(value, dict):
        raise CycleError("lead_edge_invalid")
    source_id = value.get("sourceId");pid = value.get("pid");handle = value.get("sourceHandle")
    rank = value.get("sourceRank");units = value.get("units")
    if not isinstance(source_id, str) or not source_id or not isinstance(pid, str) or not pid or \
            not isinstance(handle, str) or not handle or type(rank) is not int or rank < 1 or \
            type(units) is not int or units <= 0 or value.get("sourceKind") != "kalodata_http":
        raise CycleError("lead_edge_invalid")
    if not isinstance(value.get("windowStart"), str) or not isinstance(value.get("windowEnd"), str):
        raise CycleError("lead_edge_invalid")
    return value


def select_top_leads(edges, limit=DEFAULT_LIMIT):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise CycleError("lead_limit_invalid")
    unique = {}
    for raw in edges:
        edge = _edge(raw)
        identity = str(edge.get("kalodataCreatorId") or "handle:" + edge["sourceHandle"])
        prior = unique.get(identity)
        key = (edge["sourceRank"], -edge["units"], edge["sourceId"])
        if prior is None or key < prior[0]:
            unique[identity] = (key, edge)
    return [row[1] for row in sorted(unique.values(), key=lambda row: row[0])[:limit]]


def _require_schema(db):
    required = {"source_edge_index", "lead_query_run", "lead_query_selection", "lead_query_head"}
    found = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?)", tuple(required))}
    if found != required:
        raise CycleError("second_cycle_schema_migration_required")


def publish_query(root, *, plan_id, query_id, pid, edges, receipt_fingerprints,
                  policy_version=POLICY_VERSION, limit=DEFAULT_LIMIT, at=None):
    root = Path(root);path = root / "var/second-cycle.sqlite"
    if not path.exists():raise CycleError("plan_store_missing")
    if not isinstance(plan_id,str) or not plan_id or not isinstance(query_id,str) or not query_id or \
            not isinstance(pid,str) or not pid or not isinstance(receipt_fingerprints,list) or \
            any(not isinstance(value,str) or not value for value in receipt_fingerprints):
        raise CycleError("lead_publication_invalid")
    checked=[_edge(edge) for edge in edges]
    if any(edge["pid"]!=pid for edge in checked):raise CycleError("lead_pid_mismatch")
    selected=select_top_leads(checked,limit)
    windows={(edge["windowStart"],edge["windowEnd"]) for edge in checked}
    if len(windows)>1:raise CycleError("lead_window_mismatch")
    if windows:window_start,window_end=next(iter(windows))
    else:raise CycleError("lead_empty_window_missing")
    fingerprint=digest(sorted(receipt_fingerprints));stamp=time.time() if at is None else at
    with closing(sqlite3.connect(path,timeout=30,isolation_level=None)) as db:
        db.row_factory=sqlite3.Row;_require_schema(db);db.execute("BEGIN IMMEDIATE")
        try:
            old=db.execute("SELECT * FROM lead_query_run WHERE query_id=?",(query_id,)).fetchone()
            frozen=(plan_id,pid,window_start,window_end,policy_version,len(selected),fingerprint)
            if old:
                observed=(old["plan_id"],old["pid"],old["window_start"],old["window_end"],
                          old["policy_version"],old["selected_count"],old["receipt_fingerprint"])
                if observed!=frozen:raise CycleError("lead_publication_conflict")
            else:
                for edge in checked:
                    db.execute("INSERT OR IGNORE INTO source_edge(plan_id,source_id,payload) VALUES(?,?,?)",
                               (plan_id,edge["sourceId"],encoded(edge)))
                    db.execute("""INSERT INTO source_edge_index(
                      plan_id,source_id,pid,source_handle,source_rank,units,window_start,window_end,source_kind,
                      revenue_value,revenue_currency) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(plan_id,source_id) DO UPDATE SET
                      pid=excluded.pid,source_handle=excluded.source_handle,source_rank=excluded.source_rank,
                      units=excluded.units,window_start=excluded.window_start,window_end=excluded.window_end,
                      source_kind=excluded.source_kind,revenue_value=excluded.revenue_value,
                      revenue_currency=excluded.revenue_currency""",
                      (plan_id,edge["sourceId"],pid,edge["sourceHandle"],edge["sourceRank"],edge["units"],
                       edge["windowStart"],edge["windowEnd"],edge["sourceKind"],edge.get("revenueValue"),
                       edge.get("currency")))
                db.execute("INSERT INTO lead_query_run VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (query_id,plan_id,pid,window_start,window_end,policy_version,"published",
                            len(selected),fingerprint,stamp))
                for position,edge in enumerate(selected,start=1):
                    db.execute("INSERT INTO lead_query_selection VALUES(?,?,?,?,?)",
                               (query_id,edge["sourceId"],edge["sourceRank"],edge["units"],position))
            db.execute("INSERT INTO lead_query_head VALUES(?,?,?) ON CONFLICT(plan_id,pid) DO UPDATE SET query_id=excluded.query_id",
                       (plan_id,pid,query_id))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK");raise
    return {"queryId":query_id,"pid":pid,"rawPositive":len(checked),"selected":len(selected),
            "sourceIds":[edge["sourceId"] for edge in selected],"cached":old is not None}


def backfill_receipts(root, *, apply=False, limit=DEFAULT_LIMIT, at=None):
    """Rebuild current selections from durable local receipts; never calls Kalodata."""
    root=Path(root);leads=root/"var/kalodata-leads.sqlite";cycle=root/"var/second-cycle.sqlite"
    if not leads.exists() or not cycle.exists():raise CycleError("lead_backfill_source_missing")
    with closing(sqlite3.connect(cycle.resolve().as_uri()+"?mode=ro",uri=True)) as db:
        row=db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()
        if not row:raise CycleError("plan_missing")
        plan_id=row[0]
    with closing(sqlite3.connect(leads.resolve().as_uri()+"?mode=ro",uri=True)) as db:
        db.row_factory=sqlite3.Row
        pages=list(db.execute("SELECT pid,cursor,payload FROM leads_page ORDER BY pid,CAST(CASE WHEN cursor='' THEN '1' ELSE cursor END AS INTEGER)"))
        query_rows={row[0]:row[1] for row in db.execute("SELECT pid,window_end FROM leads_query")}
    grouped={}
    for row in pages:grouped.setdefault(str(row["pid"]),[]).append(json.loads(row["payload"]))
    index_report=backfill_source_index(root,apply=apply)
    report={"mode":"apply" if apply else "check","pids":0,"rawPositive":0,"selected":0,"missingWindow":0,
            "indexedSourceEdges":index_report["indexed"],"invalidSourceEdges":index_report["invalid"],"platformWrites":0}
    stamp=time.time() if at is None else at
    for pid,receipts in grouped.items():
        edges=[edge for receipt in receipts for edge in receipt.get("edges") or []]
        if not edges:
            end=query_rows.get(pid)
            try:
                end_day=date.fromisoformat(str(end));start=str(end_day-timedelta(days=13));end=str(end_day)
            except ValueError:
                report["missingWindow"]+=1;continue
            # Empty queries need explicit window evidence even though they publish zero rows.
            placeholder={"sourceId":"empty","pid":pid,"sourceHandle":"empty","sourceRank":1,"units":1,
                         "sourceKind":"kalodata_http","windowStart":start,"windowEnd":end}
            windows=(start,end);selected=[]
        else:
            windows=(edges[0]["windowStart"],edges[0]["windowEnd"]);selected=select_top_leads(edges,limit)
        report["pids"]+=1;report["rawPositive"]+=len(edges);report["selected"]+=len(selected)
        if apply:
            query_id="lead-backfill-"+digest([pid,windows,[r.get("rowsFingerprint") for r in receipts]])[:28]
            if edges:
                publish_query(root,plan_id=plan_id,query_id=query_id,pid=pid,edges=edges,
                              receipt_fingerprints=[str(r.get("rowsFingerprint") or digest(r)) for r in receipts],
                              limit=limit,at=stamp)
            else:
                # There is no source edge to index; publish the empty generation directly.
                with closing(sqlite3.connect(cycle,timeout=30)) as db,db:
                    _require_schema(db);fingerprint=digest([str(r.get("rowsFingerprint") or digest(r)) for r in receipts])
                    db.execute("INSERT OR IGNORE INTO lead_query_run VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (query_id,plan_id,pid,windows[0],windows[1],POLICY_VERSION,"published",0,fingerprint,stamp))
                    db.execute("INSERT INTO lead_query_head VALUES(?,?,?) ON CONFLICT(plan_id,pid) DO UPDATE SET query_id=excluded.query_id",
                               (plan_id,pid,query_id))
    return report


def backfill_source_index(root, *, apply=False):
    """Index every historical Kalodata edge so an OECID resolved on an older edge remains reusable."""
    path=Path(root)/"var/second-cycle.sqlite"
    if not path.exists():raise CycleError("plan_store_missing")
    mode="" if apply else "?mode=ro"
    with closing(sqlite3.connect(path if apply else path.resolve().as_uri()+mode,uri=not apply,timeout=30)) as db:
        db.row_factory=sqlite3.Row
        if apply:_require_schema(db)
        rows=list(db.execute("SELECT plan_id,source_id,payload FROM source_edge"))
        valid=[];invalid=0
        for row in rows:
            try:edge=_edge(json.loads(row["payload"]));valid.append((row["plan_id"],edge))
            except (CycleError,TypeError,ValueError):invalid+=1
        if apply:
            with db:
                for plan_id,edge in valid:
                    db.execute("""INSERT INTO source_edge_index(
                      plan_id,source_id,pid,source_handle,source_rank,units,window_start,window_end,source_kind,
                      revenue_value,revenue_currency) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(plan_id,source_id) DO UPDATE SET pid=excluded.pid,
                      source_handle=excluded.source_handle,source_rank=excluded.source_rank,units=excluded.units,
                      window_start=excluded.window_start,window_end=excluded.window_end,source_kind=excluded.source_kind,
                      revenue_value=excluded.revenue_value,revenue_currency=excluded.revenue_currency""",
                      (plan_id,edge["sourceId"],edge["pid"],edge["sourceHandle"],edge["sourceRank"],edge["units"],
                       edge["windowStart"],edge["windowEnd"],edge["sourceKind"],edge.get("revenueValue"),
                       edge.get("currency")))
        return {"indexed":len(valid),"invalid":invalid,"platformWrites":0}
