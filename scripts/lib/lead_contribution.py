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
