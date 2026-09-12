export type DraftStyle="friendly"|"direct"|"detailed";
export type DraftState="queued"|"running"|"drafted"|"needs_review"|"stale"|"failed"|"result_unknown";
export interface DraftRequest {packetId:string;style:DraftStyle;instructions:string;requestId:string}
export interface DraftContextRequest {packetId:string;style:DraftStyle;instructions:string}
export interface DraftSummary {
  id:string;packetId:string;status:DraftState;style:DraftStyle;createdAt:string;startedAt:string|null;finishedAt:string|null;
  errorCode:string|null;stage:null|"draft"|"review";reservedCostCny:string;knownCostCny:string|null;executionBlocked:true;
}
export interface DraftUsage {promptTokens:number|null;cacheHitTokens:number|null;cacheMissTokens:number|null;completionTokens:number|null;reasoningTokens:number|null;totalTokens:number|null}
export interface DraftCost {estimatedCny:string|null;upperBoundCny:string|null;complete:boolean}
export interface DraftContent {textIt:string;translationZh:string;selectedProductIds:string[];evidenceRefs:string[];rationaleZh:string}
export interface DraftReview {verdict:"pass"|"needs_review";issues:string[];unsupportedClaims:string[];italianValid:boolean;translationFaithful:boolean}
export interface DraftAttempt {stage:"draft"|"review";status:"inflight"|"completed"|"failed"|"result_unknown";usage:DraftUsage;cost:DraftCost}
export interface DraftFact {id:string;kind:"recipient_handle"|"product_name"|"category_alignment";value:string|string[]}
export interface DraftProduct {id:string;nameIt:string}
export interface DraftDetail {facts:DraftFact[];products:DraftProduct[];draft:DraftSummary;content:DraftContent|null;review:DraftReview|null;attempts:DraftAttempt[];cost:DraftCost;freshness:"current"|"stale"|"unknown";executionBlocked:true}
export interface DraftList {drafts:DraftSummary[];workerOnline:boolean}
export interface DraftServiceStatus {
  provider:{ready:boolean;model:"deepseek-flash"};
  policy:{enabled:boolean;id:string|null;version:number;model:"deepseek-flash";maxDrafts:number;maxCostCny:string};
  budget:{reservedCny:string;knownCostCny:string;availableCny:string;usedDrafts:number};workerOnline:boolean;
}

export type DraftReadQuery = {view:"status"}|{view:"list";packetId:string}|{view:"detail";draftId:string}|{view:"creator";creatorId:string};
