"""Bounded, read-only current-handle lookup for video authors."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time


def current_handle_owners(root, market, handles):
    # Identity observations normalize handles with Python's Unicode lower(). Search only
    # this input scope rather than materializing every identity of a large market.
    scope = sorted({str(handle).lower() for handle in handles if handle})
    if not scope:
        return {}
    path = Path(root) / 'var/creator-identities.sqlite'
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + 8
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        # Alias evidence already has an indexed (market, handle) lookup, including on
        # existing stores. The current identity remains authoritative: historical names
        # only locate candidates and cannot route an author after a rename.
        rows = db.execute('''SELECT DISTINCT i.creator_id,i.current_handle
            FROM json_each(?) h CROSS JOIN identity_observation e CROSS JOIN creator_identity i
            WHERE e.market=? AND e.handle=h.value AND e.kind='profile'
              AND i.creator_id=e.creator_id AND i.market=e.market
              AND i.handle_conflict=0 AND i.current_handle=e.handle''',
                          (json.dumps(scope), market)).fetchall()
    owners, ambiguous = {}, set()
    for creator, handle in rows:
        handle, creator = str(handle).lower(), str(creator)
        if handle in owners and owners[handle] != creator:
            ambiguous.add(handle)
        owners[handle] = creator
    return {handle: creator for handle, creator in owners.items() if handle not in ambiguous}
