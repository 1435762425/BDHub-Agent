#!/usr/bin/env python3
"""Read and mutate the local automated-operations control ledger."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.operations_workflow import (create_run, request_stop, resume_kalodata_preflight,
                                     resume_short_names, save_setting, status)  # noqa:E402
from lib.second_cycle import CycleError, CycleStore  # noqa:E402


def request_stop_for_market(store, market, run_id, expected_state="running"):
    if not isinstance(market, str) or not isinstance(run_id, str):
        raise CycleError("workflow_market_invalid")
    row = store.db.execute("SELECT market FROM workflow_run WHERE run_id=?", (run_id,)).fetchone()
    if row and row["market"] != market:
        raise CycleError("workflow_market_mismatch")
    return request_stop(store, run_id, expected_state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "save", "run", "stop", "resume-short-names",
                                           "resume-kalodata-preflight", "check-selected-recovery", "resume-selected-recovery",
                                           "resume-video-author-skip", "resume-kalodata-auth", "resume-after-fix"))
    parser.add_argument("--json")
    args = parser.parse_args()
    try:
        body = json.loads(args.json or "{}")
        market = body.get("market")
        if not isinstance(market, str):
            raise CycleError("workflow_market_invalid")
        with CycleStore(ROOT / "var/second-cycle.sqlite", readonly=args.action in ("status","check-selected-recovery")) as store:
            if args.action == "status":
                result = status(store, market)
            elif args.action in ('check-selected-recovery','resume-selected-recovery'):
                from lib.workflow_recovery import selected_catalog_evidence,resume_selected_catalog
                required={'market','runId','duplicateRunId'}|({'requestId'} if args.action=='resume-selected-recovery' else set())
                if not required<=set(body)<=required|{'isolatePids'}:raise CycleError('workflow_recovery_scope_invalid')
                isolate=body.get('isolatePids') or ()
                result=(selected_catalog_evidence(store,ROOT,market,body['runId'],body['duplicateRunId'],isolate)
                        if args.action=='check-selected-recovery' else
                        resume_selected_catalog(store,ROOT,market,body['runId'],body['duplicateRunId'],body['requestId'],isolate))
            elif args.action in ('resume-video-author-skip','resume-kalodata-auth','resume-after-fix'):
                from lib.workflow_recovery import resume_after_fix,resume_kalodata_auth,resume_video_author_skip
                if set(body)!={'market','runId','requestId'}:raise CycleError('workflow_recovery_scope_invalid')
                resume={'resume-video-author-skip':resume_video_author_skip,'resume-kalodata-auth':resume_kalodata_auth,
                        'resume-after-fix':resume_after_fix}[args.action]
                result=resume(store,market,body['runId'],body['requestId'])
            elif args.action == "save":
                result = save_setting(store, market, body.get("requestId"), body.get("expectedRevision"),
                                      body.get("changes"))
                result = status(store, market) | {"saved": result}
            elif args.action == "run":
                job=body.get('jobId');mapping={'taplink_clean':('taplink_clean',None),
                  'full_catalog_update':('catalog',['selected']),'campaign_catalog_update':('catalog',['campaign']),
                  'taplink_prepare':('taplink_prepare',None),'kalodata_leads':('kalodata',None),
                  'oecid':('oecid',None),'send_pool_publish':('send_pool',None)}
                if job is not None and job not in mapping:raise CycleError('workflow_job_invalid')
                only,sources=mapping.get(job,(None,None))
                result = create_run(store, market=market, trigger_source="manual",
                                    request_id=body.get("requestId"),only_stage=only,sources=sources)
                created=result
                result = status(store, market) | {"created": created}
            elif args.action == "stop":
                result = request_stop_for_market(store, market, body.get("runId"), body.get("expectedState", "running"))
                result = status(store, market) | {"stopped": result}
            elif args.action == "resume-short-names":
                resumed = resume_short_names(store, market, body.get("runId"), body.get("requestId"))
                result = status(store, market) | {"resumed": resumed}
            else:
                resumed = resume_kalodata_preflight(store, market, body.get("runId"), body.get("requestId"))
                result = status(store, market) | {"resumed": resumed}
        if args.action in ("run", "resume-short-names", "resume-kalodata-preflight"):
            from lib.operations_scheduler import scheduler_state,start_scheduler
            if not scheduler_state(ROOT)['running']:start_scheduler(ROOT)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (CycleError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
