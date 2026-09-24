"""Choose independently runnable market stages and reserve their durable resources."""
from __future__ import annotations

from lib.market_accounts import load_config
from lib.second_cycle import CycleError
from lib.workflow_resources import claim,recover_expired

SUPPLY_STAGES=frozenset({'taplink_clean','catalog','taplink_prepare'})
COMMUNICATION_STAGES=frozenset({'oecid'})
# Every market reaches TikTok through the same local egress, so heavy platform reads (catalog,
# TapLink, OECID) run one market at a time.  Kalodata is a different site and keeps its own slots.
PLATFORM_STAGES=SUPPLY_STAGES|COMMUNICATION_STAGES
PLATFORM_PARALLEL_MARKETS=1


def resources(root,market,stage,policy,*,accounts=None):
 if not isinstance(market,str) or not market or not isinstance(stage,str):raise CycleError('workflow_resource_scope_invalid')
 rows=(accounts if accounts is not None else load_config(root)['markets'])
 pair=rows.get(market)
 if not pair or not isinstance(pair.get('roles'),dict):raise CycleError('workflow_resource_market_unavailable')
 needed=[(f'workflow:{market}',1)]
 if stage in SUPPLY_STAGES:needed.append((f"supply:{pair['roles']['supply']}",1))
 elif stage in COMMUNICATION_STAGES:needed.append((f"communications:{pair['roles']['communications']}",1))
 elif stage=='kalodata':needed.append(('kalodata:global',policy['kalodataMaxParallelMarkets']))
 elif stage!='send_pool':raise CycleError('workflow_resource_stage_invalid')
 if stage in PLATFORM_STAGES:needed.append(('platform:global',PLATFORM_PARALLEL_MARKETS))
 return needed


def _queued(run):
 return next((row for row in run['stages'] if row['state']=='queued'),None)


def _last_success(store,market,stage):
 row=store.db.execute('''SELECT max(s.finished_at) FROM workflow_stage_run s JOIN workflow_run r ON r.run_id=s.run_id
  WHERE r.market=? AND s.stage=? AND s.state IN ('completed','quota_exhausted')''',(market,stage)).fetchone()
 return row[0] if row and row[0] is not None else 0


def _prior_generation(run,stage):
 return next((row['outputGenerationId'] for row in reversed(run['stages'][:stage['position']])
              if row['outputGenerationId']),None)


def claim_ready(store,root,runs,policy,owner_id,*,max_parallel=14,accounts=None,worker_pid=None,pid_alive=None,lease_seconds=300):
 """Reserve ready stages by oldest success, then existing checkpoint, then market."""
 if type(max_parallel) is not int or not 1<=max_parallel<=14:raise CycleError('workflow_parallel_limit_invalid')
 recovered=recover_expired(store,pid_alive=pid_alive)
 ready=[]
 for run in runs:
  if run['state'] not in ('queued','running'):continue
  stage=_queued(run)
  if stage:ready.append((_last_success(store,run['market'],stage['stage']),
                         0 if stage.get('checkpoint') else 1,run['market'],run,stage))
 ready.sort(key=lambda row:row[:3]);claimed=[]
 for _,_,market,run,stage in ready:
  if len(claimed)>=max_parallel:break
  needed=resources(root,market,stage['stage'],policy,accounts=accounts)
  try:
   ticket=claim(store,stage['stageRunId'],owner_id,needed,worker_pid=worker_pid,lease_seconds=lease_seconds,
                input_generation_id=_prior_generation(run,stage))
  except CycleError as error:
   if str(error)=='workflow_resource_busy':continue
   raise
  if ticket['duplicate']:continue
  claimed.append({'run':run,'stage':stage,'ticket':ticket,'resources':needed})
 return {'claimed':claimed,'recovered':recovered}
