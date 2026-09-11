import type {SecondPilotCase} from "../../features/second-pilot/contracts.ts";

export function rehearsalDecision(current:SecondPilotCase):{kind:"skip";reason:"paused"|"completed"|"unknown"|"already_queued"}|{kind:"queue";freezeRequestId:string}{
  if(current.localControl==="paused")return {kind:"skip",reason:"paused"};
  if(current.actions.some(a=>a.status==="result_unknown"||a.status==="submitting"))return {kind:"skip",reason:"unknown"};
  if(current.actions.some(a=>a.status==="simulated_accepted"))return {kind:"skip",reason:"completed"};
  if(current.actions.some(a=>a.status==="queued"))return {kind:"skip",reason:"already_queued"};
  return {kind:"queue",freezeRequestId:`rehearsal-freeze:${current.id}:r${current.revision}:c${current.localControlRevision}`};
}
