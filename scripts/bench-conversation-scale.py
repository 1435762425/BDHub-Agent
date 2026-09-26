#!/usr/bin/env python3
"""Measure the conversation read paths on a synthetic ledger (G18); never opens ``var/``.

Builds a throwaway second-cycle database in a temporary directory with ``--relations`` creators, a share of
which carry inbound history, open work or human mode, then records per-view list cost (SQL statements,
relations scanned, elapsed) and the reply-projection backfill (first build and steady state).  The numbers are
a baseline for review, not acceptance thresholds.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.conversation_workbench import list_conversations  # noqa: E402
from lib.cycle_auto_reply import AutoReplies  # noqa: E402
from lib.cycle_inbox import Inbox  # noqa: E402
from lib.cycle_service import Service  # noqa: E402
from lib.reply_events import backfill  # noqa: E402
from lib.schema_migrations import apply_database  # noqa: E402
from lib.second_cycle import CycleStore  # noqa: E402

NOW = 1_790_000_000.0


def build(root, relations, history, pending, human):
    (root / "var").mkdir()
    path = root / "var/second-cycle.sqlite"
    # Production created these tables before the migrations added their read indexes: keep that order.
    with CycleStore(path, lambda: NOW) as store:
        plan = store.plan("bjn-local-research", "it")
        Inbox(store)
        Service(store)
        AutoReplies(store)
    apply_database(root, "second-cycle", clock=lambda: NOW)
    store = CycleStore(path, lambda: NOW)
    rel, events, versions, heads, pend, checkpoints = [], [], [], [], [], []
    for n in range(relations):
        creator, oec, cid = f"creator_{n:06d}", str(7_000_000 + n), str(9_000_000 + n)
        mode = "human" if n < relations * human else "auto"
        rel.append((plan, creator, oec, mode, 0, 1, 0, 1))
        if n < relations * history:
            at = NOW - 60 * (n % 10_000)
            message = str(50_000_000 + n)
            payload = json.dumps({"conversationId": cid, "oecId": oec, "messageId": message, "kind": "creatorReplies"})
            events.append((plan, cid, message, oec, "creatorReplies", int(at * 1000), payload, 0, at))
            body = json.dumps({"format": "text", "text": f"domanda {n}"})
            versions.append((plan, cid, message, f"h{n}", body, at))
            heads.append((plan, cid, message, f"h{n}"))
            checkpoints.append((plan, cid, oec, at - 60, at, "tracking"))
            if n < relations * pending:
                pend.append((plan, creator, 1, at, "awaiting_classification"))
    with store.tx():
        store.db.executemany("INSERT INTO relationship VALUES(?,?,?,?,?,?,?,?)", rel)
        store.db.executemany("INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,?,?)", events)
        store.db.executemany("INSERT INTO inbox_content_version VALUES(?,?,?,?,?,?)", versions)
        store.db.executemany("INSERT INTO inbox_content_head VALUES(?,?,?,?)", heads)
        store.db.executemany("INSERT INTO inbox_pending VALUES(?,?,?,?,?)", pend)
        store.db.executemany("INSERT INTO inbox_checkpoint VALUES(?,?,?,?,?,?)", checkpoints)
    return store


def timed(function):
    started = time.perf_counter()
    value = function()
    return value, round((time.perf_counter() - started) * 1000, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relations", type=int, default=50_000)
    parser.add_argument("--history", type=float, default=0.2, help="share of creators with an inbound message")
    parser.add_argument("--pending", type=float, default=0.02, help="share with unanswered work")
    parser.add_argument("--human", type=float, default=0.01, help="share in human mode")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="bdhub-scale-bench-") as folder:
        root = Path(folder)
        store = build(root, args.relations, args.history, args.pending, args.human)
        try:
            first, first_ms = timed(lambda: backfill(store))
            _, steady_ms = timed(lambda: backfill(store))
            views = {}
            for view in ("human", "agent", "completed", "all"):
                page, elapsed = timed(lambda view=view: list_conversations(root, store, view, limit=100))
                next_ms = None
                if page["nextCursor"]:
                    _, next_ms = timed(lambda view=view, cursor=page["nextCursor"]:
                                           list_conversations(root, store, view, limit=100, after=cursor))
                views[view] = {"total": page["total"], "elapsedMs": elapsed, "nextPageMs": next_ms,
                               **{key: page["stats"][key] for key in ("relationsScanned", "candidates", "rows", "sqlStatements")}}
        finally:
            store.close()
    print(json.dumps({"relations": args.relations, "history": args.history, "pending": args.pending,
                      "backfill": {"firstMs": first_ms, "turnsAdded": first["turnsAdded"], "steadyMs": steady_ms},
                      "list": views, "syntheticOnly": True}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
