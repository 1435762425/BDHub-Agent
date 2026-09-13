#!/usr/bin/env python3
"""One historical incoming text shadow evaluation. Does not queue or send replies."""
import sys,json
from pathlib import Path
from lib.second_cycle import CycleStore,CycleError
from lib.cycle_agent import AgentEvaluation
ROOT=Path(__file__).resolve().parents[1]
def main():
 from lib.draft_provider import call_model
 with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  row=store.db.execute('''SELECT v.payload FROM inbox_content_head h JOIN inbox_content_version v ON v.plan_id=h.plan_id AND v.cid=h.cid AND v.message_id=h.message_id AND v.hash=h.hash JOIN inbox_event e ON e.plan_id=h.plan_id AND e.cid=h.cid AND e.message_id=h.message_id WHERE e.historical=1 AND e.kind='creatorReplies' AND json_extract(v.payload,'$.format')='text' ORDER BY e.occurred_ms DESC LIMIT 1''').fetchone()
  if not row:raise CycleError('no_historical_text')
  result=AgentEvaluation(store).evaluate([json.loads(row[0])],call_model)
  print(json.dumps({k:result[k] for k in ('id','category','requiredTools','automaticReply','cached','usage') if k in result},ensure_ascii=False))
if __name__=='__main__':main()
