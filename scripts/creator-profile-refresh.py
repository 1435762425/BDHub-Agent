#!/usr/bin/env python3
"""Enqueue/status a fixed OEC profile refresh, or run the local refresh worker."""
import argparse
import json
import signal
import sys
import time
import traceback

from lib.profile_refresh import ROOT, ProfileRefreshError, ProfileRefreshStore, ProfileRefreshWorker
from lib.creator_discovery import CreatorDiscoveryError, CreatorDiscoveryStore, CreatorDiscoveryWorker


CRASH_LOG = ROOT / "var/identity-worker-crash.log"


def record_crash(error):
    """Keep the reason behind ``internal_error`` -- without publishing raw exception text.

    ``internal_error`` on its own is undiagnosable: on 2026-09-15 it was the only thing left of a
    stopped backfill. But the exception *message* is not safe to publish either -- it can carry a
    credential or a remote body -- so the full traceback goes to a local log, and only the exception
    *type* is announced on stderr, which is what the batch driver stores in its report and the page
    shows. That is enough to tell "the database was locked" from "a field was missing" without
    putting anything the platform or a credential said into an API answer.
    """
    detail = traceback.format_exc()
    try:
        with CRASH_LOG.open("a", encoding="utf-8") as stream:
            stream.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} {type(error).__name__}: {error} ===\n{detail}\n")
        CRASH_LOG.chmod(0o600)
    except OSError:
        # A worker that cannot write its own crash log must still report the crash.
        pass
    try:
        where = CRASH_LOG.relative_to(ROOT)
    except ValueError:
        where = CRASH_LOG
    print(f"identity_worker_crash {type(error).__name__} -- traceback: {where}", file=sys.stderr)


def envelope(code, message, status):
    return json.dumps({"error": {"code": code, "message": message, "status": status}})


def read_request(fields):
    raw = sys.stdin.read(4097)
    if len(raw) > 4096:
        raise ProfileRefreshError("invalid_request")
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise ProfileRefreshError("invalid_request") from error
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ProfileRefreshError("invalid_request")
    return value


def reconcile_cycle(var_dir,discovery_store):
    path=var_dir/'second-cycle.sqlite'
    if not path.exists():return None
    try:
        from lib.second_cycle import CycleStore
        from lib.cycle_identity import IdentityBridge
        with CycleStore(path) as cycle:
            plan=cycle.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()
            if not plan:return None
            bridge=IdentityBridge(cycle,discovery_store,var_dir/'creator-identities.sqlite')
            if cycle._plan(plan[0])['state']=='active':
                bridge.freeze(plan[0])
                bridge.dispatch(plan[0])
            return bridge.reconcile(plan[0])
    except Exception:
        # An optional supply projection must not take down manual identity work.
        return {'status':'blocked','error':'cycle_identity_reconcile_failed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("enqueue")
    commands.add_parser("status")
    commands.add_parser("list")
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--soak-run")
    worker.add_argument("--cohort-size",type=int,choices=(1,10,20),default=1)
    worker.add_argument("--cohort-lanes",type=int,choices=(3,6,9),default=3)
    worker.add_argument("--only-batch")
    worker.add_argument("--interval", type=float, default=5)
    # 达人级一次：判过的 handle 不再重复问平台（补 OECID 的驱动器会带上它；soak/验收默认不带）。
    worker.add_argument("--skip-judged", action="store_true")
    args = parser.parse_args()
    try:
        with ProfileRefreshStore() as store:
            if args.command == "enqueue":
                value = read_request(("creatorId", "requestId"))
                print(json.dumps(store.enqueue(value["creatorId"], value["requestId"])))
            elif args.command == "status":
                value = read_request(("jobId",))
                print(json.dumps(store.status(value["jobId"])))
            elif args.command == "list":
                value = read_request(("creatorId",))
                print(json.dumps(store.list_jobs(value["creatorId"])))
            else:
                if not 0.1 <= args.interval <= 60:
                    raise ProfileRefreshError("invalid_request")
                def stop(*_):
                    raise KeyboardInterrupt()
                signal.signal(signal.SIGTERM, stop)
                with CreatorDiscoveryStore(store.var_dir) as discovery_store, ProfileRefreshWorker(store) as runner, CreatorDiscoveryWorker(discovery_store) as discovery:
                    while True:
                        # Both lanes use ACC6 and run sequentially. Each round
                        # takes at most one refresh and one discovery item.
                        refreshed = runner.run_once()
                        cohort_result=None
                        if args.cohort_size>1:
                            from lib.discovery_cohort import run_cohort
                            cohort_result=run_cohort(discovery,args.cohort_size,lanes=args.cohort_lanes,
                                soak_id=args.soak_run,only_batch=args.only_batch,
                                use_production_policy=True,skip_judged=args.skip_judged)
                        discovered = None if cohort_result else discovery.run_once()
                        cycle_result = reconcile_cycle(store.var_dir,discovery_store)
                        if refreshed is not None or discovered is not None or cohort_result is not None or args.once:
                            print(json.dumps({"refresh": refreshed, **({"cohort":cohort_result} if cohort_result else {}), "discovery": discovered["batch"] if discovered else None,**({"cycleIdentity":cycle_result} if cycle_result is not None else {})}), flush=True)
                        if args.once or cohort_result and cohort_result.get('soakState')=='attention':break
                        if cohort_result and cohort_result.get('soakState')=='completed':
                            from lib.identity_acceptance import production_policy
                            if production_policy(store.var_dir.parent)['acceptanceId']:
                                args.soak_run=None
                            else:break
                        time.sleep(15 if args.soak_run and cohort_result and cohort_result.get('targets')==0 else args.interval)
        return 0
    except (ProfileRefreshError, CreatorDiscoveryError) as error:
        # A cohort-level refusal -- a locked policy read, a malformed probe report -- carries its own
        # code. Flattening it into ``internal_error`` says "we do not know" when we do know.
        print(envelope(error.code, error.code, error.status))
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        record_crash(error)
        print(envelope("internal_error", "Profile refresh service error", 500))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
