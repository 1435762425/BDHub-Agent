export type CreatorDiscoveryCommand="preview"|"submit"|"list"|"detail"|"control";
export type DiscoveryBatchStatus="queued"|"running"|"paused"|"completed"|"blocked";
export type DiscoveryItemStatus="queued"|"running"|"completed"|"unresolved"|"blocked"|"invalid"|"duplicate";
export interface DiscoveryPreviewInput {market:"it";sourceLabel:string;text:string}
export interface DiscoverySubmitInput extends DiscoveryPreviewInput {previewHash:string;requestId:string}
export interface DiscoveryControlInput {batchId:string;action:"pause"|"resume";requestId:string}
export type CreatorDiscoveryPost =
  | ({command:"preview"}&DiscoveryPreviewInput)
  | ({command:"submit"}&DiscoverySubmitInput)
  | ({command:"control"}&DiscoveryControlInput);
export interface DiscoveryPreviewItem {index:number;raw:string;handle:string|null;status:"valid"|"duplicate"|"invalid";reason:string|null;duplicateOf:number|null}
export interface DiscoveryPreview {
  market:"it";sourceLabel:string;previewHash:string;counts:{total:number;valid:number;duplicate:number;invalid:number};
  items:DiscoveryPreviewItem[];canSubmit:boolean;
}
export interface DiscoveryBatchCounts {
  total:number;queued:number;running:number;completed:number;unresolved:number;blocked:number;invalid:number;duplicate:number;
  created:number;existing:number;identityOnly:number;
}
export interface DiscoveryBatch {
  id:string;market:"it";sourceLabel:string;status:DiscoveryBatchStatus;createdAt:string;
  startedAt:string|null;finishedAt:string|null;errorCode:string|null;counts:DiscoveryBatchCounts;workerOnline:boolean;
}
export interface DiscoveryBatchItem {
  id:string;index:number;handle:string|null;status:DiscoveryItemStatus;reason:string|null;duplicateOf:number|null;
  creatorId:string|null;oecId:string|null;startedAt:string|null;finishedAt:string|null;requestCount:number|null;
  outcome:"created"|"existing"|"identity_only"|null;
}
export interface DiscoveryList {batches:DiscoveryBatch[];workerOnline:boolean}
export interface DiscoveryDetail {batch:DiscoveryBatch;items:DiscoveryBatchItem[]}
export type CreatorDiscoveryResponse=DiscoveryPreview|DiscoveryBatch|DiscoveryList|DiscoveryDetail;
