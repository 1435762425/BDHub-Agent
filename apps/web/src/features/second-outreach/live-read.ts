import type {SecondLiveResponse,SecondLiveStartRequest} from "./live-contracts.ts";
export const LIVE_TRIAL_ID=/^second_live_trial_[a-f0-9]{32}$/;
const HASH=/^[a-f0-9]{64}$/;
export function validStartRequest(value:unknown):value is SecondLiveStartRequest {
  if(!value||typeof value!=="object"||Array.isArray(value))return false;
  const row=value as SecondLiveStartRequest;
  return Object.keys(row).length===4&&LIVE_TRIAL_ID.test(row.trialId)&&HASH.test(row.snapshotHash)&&row.confirmed===true&&typeof row.requestId==="string"&&/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(row.requestId);
}
export function validLiveResponse(value:unknown,trialId:string):value is SecondLiveResponse {
  if(!value||typeof value!=="object")return false;const result=value as SecondLiveResponse;
  return result.trial?.trialId===trialId&&HASH.test(result.trial.snapshotHash)&&result.trial.market==="it"&&result.trial.account==="acc6"&&result.trial.campaignId==="italy-second-pilot"&&[result.trial.approved,result.trial.paused,result.trial.expired,result.trial.complete].every(value=>typeof value==="boolean")&&Number.isFinite(Date.parse(result.trial.expiresAt))&&Array.isArray(result.trial.blockedByUnknown)&&Array.isArray(result.trial.items)&&result.trial.items.length>0&&result.trial.items.length<=3&&result.trial.items.every(item=>typeof item.itemId==="string"&&typeof item.textIt==="string"&&typeof item.translationZh==="string"&&Array.isArray(item.components)&&Array.isArray(item.attempts))&&["not_started","started","running","finished","start_unknown"].includes(result.launch?.state);
}
/** No query parameter means no request: never choose a sample or latest batch. */
export async function readLiveTrial(trialId:string,get:(url:string)=>Promise<unknown>):Promise<SecondLiveResponse|null>{
  if(!trialId)return null;if(!LIVE_TRIAL_ID.test(trialId))throw new Error("实测批次编号不正确，请使用完整批次链接。");
  const value=await get(`/api/second-live?trialId=${encodeURIComponent(trialId)}`);
  if(!validLiveResponse(value,trialId))throw new Error("实测数据与本次冻结批次不一致。");return value;
}
export function liveStartBlocker(value:SecondLiveResponse|null,now=Date.now()):string|null {
  if(!value)return "loading";const trial=value.trial;
  if(trial.paused)return "paused";
  if(trial.expired||Date.parse(trial.expiresAt)<=now)return "expired";
  if(trial.complete)return "complete";
  if(trial.blockedByUnknown.length||trial.items.some(item=>item.state==="result_unknown"||item.requiresReconciliation||item.components.some(component=>component.state==="result_unknown")))return "unknown";
  if(trial.items.some(item=>item.partialDelivery||item.state==="partial_delivery"))return "partial";
  if(value.launch.state!=="not_started"||value.launch.requestId!==null)return "launch_recorded";
  if(trial.items.some(item=>item.state!=="pending"||item.attempts.length||item.components.some(component=>component.state!=="pending")))return "already_started";
  if(trial.items.some(item=>{const ordered=[...item.components].sort((a,b)=>a.position-b.position);return ordered.length<2||ordered.at(-1)?.componentKind!=="text"||ordered.slice(0,-1).some(component=>component.componentKind!=="card");}))return "components_missing";
  return null;
}
