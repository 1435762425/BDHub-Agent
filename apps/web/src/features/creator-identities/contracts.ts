export type IdentityMarket = "it" | "mx" | "br";
export type IdentityDatasetStatus = "ready" | "not_imported";
export type IdentityListStatus = "verified" | "pending";
export type ProfileFieldStatus = "absent" | "no_value" | "unauthorized" | "error" | "zero" | "value";
export type ProfileFieldValue = string | number | boolean | null | ProfileFieldValue[] | {[key:string]:ProfileFieldValue};

export interface CreatorIdentitySummary {
  kind:"creator"; status:"verified"; creatorId:string; market:IdentityMarket; oecId:string;
  currentHandle:string|null; currentHandleVerifiedAt:string|null;
  verifiedAt:string; lastObservedAt:string; handleConflict:boolean;
}
export interface PendingIdentityLead {
  kind:"lead"; status:"pending"; leadId:string; market:IdentityMarket; handle:string; observedAt:string;
  historicalCrossSourceIdentityProven:false;
}
export interface CreatorIdentityList {
  datasetStatus:IdentityDatasetStatus; market:IdentityMarket; status:IdentityListStatus;
  items:(CreatorIdentitySummary|PendingIdentityLead)[]; total:number; offset:number; limit:number;
}
export interface IdentityOverviewCounts {
  verifiedIdentities:number; pendingLeads:number; resolvedLeads:number; observations:number; lastVerifiedAt:string|null;
}
export interface CreatorIdentityOverview extends IdentityOverviewCounts {
  datasetStatus:IdentityDatasetStatus; market:IdentityMarket; repliedCreators:number|null;showcaseCreators:number|null;
  markets:({market:IdentityMarket}&IdentityOverviewCounts)[];
}
export interface IdentityAlias {
  handle:string; firstObservedAt:string; lastObservedAt:string; isCurrent:boolean;
}
export interface IdentityProfileField {
  name:string; status:ProfileFieldStatus; value?:ProfileFieldValue; observedAt:string|null;
  lastAvailable?:{status:"value"|"zero";value:ProfileFieldValue;observedAt:string};
}
export interface CreatorIdentityDetail {
  datasetStatus:IdentityDatasetStatus; creator:CreatorIdentitySummary|null; aliases:IdentityAlias[];
  latestProfileObservedAt:string|null; fields:IdentityProfileField[];
  latestObservation:{kind:"profile"|"failure";outcome:"observed"|"unknown"|"timeout"|"not_found"|"blocked"|"error";observedAt:string}|null;
}
export interface IdentitySourceResolution {
  datasetStatus:IdentityDatasetStatus; market:IdentityMarket; queryKind:"oec"|"external_source";
  status:"verified"|"pending"|"not_found"|"ambiguous"|"not_imported";
  resolutionRelation:"canonical_oec"|"current_discovery_only";
  historicalCrossSourceIdentityProven:false;
  creator:CreatorIdentitySummary|null; candidates:CreatorIdentitySummary[]; candidateCount:number; candidatesTruncated:boolean;
  pendingLeadCount:number; resolvedLeadCount:number;
}
export type CreatorIdentityQuery =
  | {view:"overview";market:IdentityMarket}
  | {view:"list";market:IdentityMarket;status:IdentityListStatus;q:string;offset:number;limit:number}
  | {view:"detail";creatorId:string}
  | {view:"source";market:IdentityMarket;oecId?:string;externalId?:string};
