"""Let a long platform stage step aside for another market at a safe boundary (H14).

A long child (Campaign collection, TapLink batches) calls ``yield_platform`` between two of its
own checkpointed steps. When another market's stage is queued for the shared platform read slot,
the slot is lent out, the dispatcher gets time to hand it over, and the child waits to take it
back before its next platform read. The claim, its fence and every other slot stay with the stage;
without the scheduler's claim in the environment (manual runs, tests) this is a no-op.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ENV = "BDHUB_STAGE_CLAIM"
PLATFORM = "platform:global"
HANDOVER_SECONDS = 45  # the resident dispatcher refills free slots at least every 30 s
RECLAIM_WAIT_SECONDS = 3600


def claim_from_env():
    try:
        value = json.loads(os.environ.get(ENV) or "null")
    except ValueError:
        return None
    if not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in ("stageRunId", "ownerId")) \
            or type(value.get("fence")) is not int:
        return None
    return value


def others_waiting(store, stage_run_id):
    """Another market has a queued platform stage in an active run."""
    from lib.workflow_dispatch import PLATFORM_STAGES
    marks = ",".join("?" * len(PLATFORM_STAGES))
    return bool(store.db.execute(f"""SELECT 1 FROM workflow_stage_run s JOIN workflow_run r ON r.run_id=s.run_id
        WHERE s.state='queued' AND s.stage IN ({marks}) AND r.state IN ('queued','running') AND s.stage_run_id<>?
          AND r.market<>(SELECT r2.market FROM workflow_stage_run s2 JOIN workflow_run r2 ON r2.run_id=s2.run_id
                         WHERE s2.stage_run_id=?) LIMIT 1""", (*sorted(PLATFORM_STAGES), stage_run_id, stage_run_id)).fetchone())


def yield_platform(root, *, sleep=time.sleep, monotonic=time.monotonic):
    """Step aside once if someone waits; returns seconds yielded (0 when nothing happened).

    Raises ``platform_slot_reclaim_timeout`` if the slot cannot be taken back: the caller must stop
    rather than read the platform without it."""
    claim = claim_from_env()
    if not claim:
        return 0
    from lib.second_cycle import CycleError, CycleStore
    from lib.workflow_dispatch import PLATFORM_PARALLEL_MARKETS
    from lib.workflow_resources import lend_slot, reclaim_slot
    database = Path(root) / "var/second-cycle.sqlite"
    args = (claim["stageRunId"], claim["ownerId"], claim["fence"])
    with CycleStore(database) as store:
        if not others_waiting(store, claim["stageRunId"]) or not lend_slot(store, *args, PLATFORM):
            return 0
    started = monotonic()
    sleep(HANDOVER_SECONDS)
    while True:
        with CycleStore(database) as store:
            if reclaim_slot(store, *args, PLATFORM, PLATFORM_PARALLEL_MARKETS):
                return round(monotonic() - started, 1)
        if monotonic() - started >= RECLAIM_WAIT_SECONDS:
            raise CycleError("platform_slot_reclaim_timeout")
        sleep(10)
