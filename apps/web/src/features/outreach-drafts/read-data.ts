import type {DraftDetail,DraftList,DraftServiceStatus,DraftSummary} from "./contracts.ts";
const API="/api/outreach-drafts";
export function isDraftSummary(value:unknown):value is DraftSummary {
  const draft=value as DraftSummary|null;
  return Boolean(draft&&typeof draft.id==="string"&&typeof draft.packetId==="string"&&["queued","running","drafted","needs_review","stale","failed","result_unknown"].includes(draft.status)&&["friendly","direct","detailed"].includes(draft.style)&&draft.executionBlocked===true);
}
/** Reading an archived draft does not depend on current model configuration. */
export async function readDraftPanelData({packetId,selectedId,readOnly}:{packetId:string;selectedId:string;readOnly:boolean},get:(url:string)=>Promise<unknown>):Promise<{service:DraftServiceStatus|null;list:DraftList;detail:DraftDetail|null;selectedId:string}> {
  const [rawService,rawList]=await Promise.all([readOnly?Promise.resolve(null):get(`${API}?view=status`),get(`${API}?view=list&packetId=${encodeURIComponent(packetId)}`)]);
  const service=rawService as DraftServiceStatus|null,list=rawList as DraftList|null;
  if(!readOnly&&(typeof service?.provider?.ready!=="boolean"||typeof service?.policy?.enabled!=="boolean"||!service.budget))throw new Error("模型服务状态响应不完整。");
  if(!list||!Array.isArray(list.drafts)||!list.drafts.every(item=>isDraftSummary(item)&&item.packetId===packetId))throw new Error("草稿列表与当前资料包不一致。");
  const id=selectedId||list.drafts[0]?.id||"",detail=id?await get(`${API}?view=detail&draftId=${encodeURIComponent(id)}`) as DraftDetail:null;
  if(detail&&(!isDraftSummary(detail.draft)||detail.draft.id!==id||detail.draft.packetId!==packetId||detail.executionBlocked!==true||!["current","stale","unknown"].includes(detail.freshness)))throw new Error("草稿详情与当前资料包不一致。");
  return {service,list,detail,selectedId:id};
}
