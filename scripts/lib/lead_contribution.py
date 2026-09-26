"""What one Kalodata publication added, measured at the publication itself (H13, second layer).

Counted inside the publishing transaction, before its rows are written, and stored once per
publication: running the same query again never counts twice. "New" is relative to what this
market's plan already held for the product (pairs) or at all (creators); a refresh of a known pair
is reported separately and never as a new lead. Later identity, sendability and replies are not
attributed here.
"""

SQL = ("CREATE TABLE IF NOT EXISTS lead_publication_contribution(publication_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,"
       "market TEXT NOT NULL,pid TEXT NOT NULL,kind TEXT NOT NULL,handles INTEGER NOT NULL,new_pairs INTEGER NOT NULL,"
       "refreshed_pairs INTEGER NOT NULL,new_creators INTEGER NOT NULL,published_at REAL NOT NULL)")


def record(db, *, publication_id, plan_id, pid, kind, handles, at):
    """Must run inside the publishing transaction and before this publication's edges are written."""
    db.execute(SQL)
    handles = {str(handle).lower() for handle in handles if handle}
    market = db.execute("SELECT market FROM plan WHERE id=?", (plan_id,)).fetchone()
    for_pid = {str(row[0]).lower() for row in db.execute(
        "SELECT source_handle FROM source_edge_index WHERE plan_id=? AND pid=?", (plan_id, str(pid)))}
    seen = set()
    ordered = sorted(handles)
    for start in range(0, len(ordered), 500):
        chunk = ordered[start:start + 500]
        marks = ",".join("?" * len(chunk))
        seen.update(str(row[0]).lower() for row in db.execute(
            f"SELECT DISTINCT lower(source_handle) FROM source_edge_index WHERE plan_id=? AND lower(source_handle) IN ({marks})",
            (plan_id, *chunk)))
    db.execute("INSERT OR IGNORE INTO lead_publication_contribution VALUES(?,?,?,?,?,?,?,?,?,?)",
               (publication_id, plan_id, market[0] if market else "", str(pid), kind, len(handles),
                len(handles - for_pid), len(handles & for_pid), len(handles - seen), at))


def between(db, market, started, finished):
    """Sum of the contributions a stage published in its own time window; None when not recorded."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='lead_publication_contribution'").fetchone():
        return None
    row = db.execute("""SELECT count(*),coalesce(sum(new_pairs),0),coalesce(sum(refreshed_pairs),0),coalesce(sum(new_creators),0),
        coalesce(sum(kind='A'),0),coalesce(sum(kind='B'),0) FROM lead_publication_contribution
        WHERE market=? AND published_at>=? AND published_at<=?""", (market, started, finished)).fetchone()
    if not row or not row[0]:
        return None
    return {"publications": row[0], "newPairs": row[1], "refreshedPairs": row[2], "newCreators": row[3],
            "aPublications": row[4], "bPublications": row[5]}


def later_outcomes(db, market, since, now):
    """What became of the leads published since ``since`` (H13, third layer); read-only.

    Each delivery is credited only to the source it froze when it was prepared (never to every source
    that also listed the creator). Replies count creators whose first non-historical inbound message
    came after that delivery's confirmed card. Correlation over an open observation window, not a
    causal conversion and no revenue."""
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'plan', 'lead_query_selection', 'lead_query_run', 'cycle_delivery', 'cycle_delivery_part'} <= tables:
        return None
    plan = db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'", (market,)).fetchone()
    if not plan:
        return None
    plan = plan[0]
    sources = {'A': {row[0] for row in db.execute("""SELECT s.source_id FROM lead_query_selection s
        JOIN lead_query_run q ON q.query_id=s.query_id WHERE q.plan_id=? AND q.published_at>=?""", (plan, since))}, 'B': set()}
    if {'source_edge_index', 'kalodata_video_run'} <= tables:
        sources['B'] = {row[0] for row in db.execute("""SELECT e.source_id FROM source_edge_index e JOIN kalodata_video_run r
            ON r.run_id=substr(e.source_id,7,instr(substr(e.source_id,7),':')-1)
            WHERE e.plan_id=? AND e.source_kind='kalodata_video' AND r.observed_at>=?""", (plan, since))}
    deliveries = list(db.execute("""SELECT d.id,d.creator_id,d.state,d.source_id,
        (SELECT min(p.started) FROM cycle_delivery_part p WHERE p.delivery_id=d.id AND p.kind='card' AND p.state='confirmed') card_at
        FROM cycle_delivery d WHERE d.plan_id=? AND d.created>=?""", (plan, since)))
    first_reply = {}
    if 'inbound_turn' in tables:
        for creator, at in db.execute("""SELECT creator_id,coalesce(occurred_ms/1000.0,observed_at) FROM inbound_turn
            WHERE plan_id=? AND historical=0 AND coalesce(occurred_ms/1000.0,observed_at)>=?""", (plan, since)):
            first_reply.setdefault(creator, []).append(at)
    result = {'since': since, 'asOf': now}
    for kind, ids in sources.items():
        used = [row for row in deliveries if row[3] in ids]
        sent = [row for row in used if row[2] in ('confirmed', 'partial_delivery') and row[4]]
        creators = {row[1] for row in sent}
        replied = {row[1] for row in sent if any(at > row[4] for at in first_reply.get(row[1], []))}
        result[kind] = {'publishedSources': len(ids), 'deliveries': len(used), 'sentDeliveries': len(sent),
                        'creatorsReached': len(creators), 'creatorsReplied': len(replied)}
    return result
