export type MatchMarket = "mx" | "br" | "it";
export type MatchCurrency = "MXN" | "BRL" | "EUR";
export type ContentFormat = "video" | "live";
export interface FactSource {ref:string; observedAt:number; windowStart:number|null; windowEnd:number|null; windowBasis?:"calendar_date_unknown_timezone";}
export interface MatchProduct {
  id:string; market:MatchMarket; pid:string; title:string; image:string;
  categories:string[]; formats:ContentFormat[]; description:string;
  priceMinor:number|null; currency:MatchCurrency; source:FactSource;
  semanticRevision:number; commercialRevision:number;
}
export interface MatchOffer {
  id:string; productId:string; campaignId:string; accountRef:string;
  publicCommissionBps:number|null; totalCommissionBps:number|null;
  creatorCommissionBps:number|null; agencyCommissionBps:number|null;
  stock:number|null; sampleAvailable:boolean|null; sampleQuota:number|null;
  startsAt:number; endsAt:number; cardStatus:"verified"|"needs_preparation"|"unknown";
  source:FactSource; version:number;
}
export interface MatchCreator {
  id:string; market:MatchMarket; oecId:string|null; name:string; avatar:string;
  externalIdentity?:{namespace:"kalodata";id:string};
  categories:string[]; formats:ContentFormat[]; bio:string;
  priceMinMinor:number|null; priceMaxMinor:number|null; currency:MatchCurrency;
  control:"auto"|"human"|"paused"|"unknown"; marketingStopped:boolean|null;
  source:FactSource; semanticRevision:number; relationRevision:number;
}
export interface ProductEvidence {
  id:string; creatorId:string; market:MatchMarket; pid:string;
  units:number; format:ContentFormat|null; source:FactSource;
}
export interface CreatorDemand {
  id:string; creatorId:string; productId:string|null; categories:string[];
  active:boolean; source:FactSource;
}
export type ProductInput = Omit<MatchProduct,"semanticRevision"|"commercialRevision">;
export type CreatorInput = Omit<MatchCreator,"semanticRevision"|"relationRevision">;
export type OfferInput = Omit<MatchOffer,"version">;
export interface MatchingBatch {products?:ProductInput[]; creators?:CreatorInput[]; offers?:OfferInput[]; evidence?:ProductEvidence[]; demands?:CreatorDemand[];}
export interface ImportResult {inserted:number; updated:number; unchanged:number; semanticChanges:number; commercialChanges:number; relationChanges:number;}
export interface MatchingDataset {
  id:string; mode:"synthetic-local"|"imported-offline"; label:string;
  importedAt:number|null; sourceRefs:string[]; warnings:string[];
}
export interface MatchingStats {
  mode:MatchingDataset["mode"]; dataset:MatchingDataset; products:number; creators:number; offers:number; evidence:number;
  demands:number; runs:number; packets:number; semanticBuilds:number;
  llmCalls:0; billedTokens:0; matchingVersion:string;
}
export interface MatchPage<T> {items:T[]; total:number; offset:number; limit:number;}
export interface RecallQuery {
  direction:"product"|"creator"; subjectId:string; source:"all"|"first"|"second";
  limit:number;
}
export type CandidateSource = "exact_pid"|"explicit_demand"|"category_price"|"category"|"cold_start";
export interface MatchCandidate {
  creator:MatchCreator; product:MatchProduct; offers:MatchOffer[];
  sources:CandidateSource[]; reasons:string[]; gaps:string[]; evidenceRefs:string[];
  readiness:"reviewable"|"needs_facts"|"suppressed";
  features:{categoryOverlap:number; priceOverlap:boolean|null; formatOverlap:boolean|null; exactUnits:number|null;};
}
export interface MatchRun {
  id:string; query:RecallQuery; market:MatchMarket; createdAt:number;
  matchingVersion:string; fingerprint:string; cacheHit:boolean; stale:boolean;
  subject:MatchProduct|MatchCreator; candidates:MatchCandidate[];
  diagnostics:{rowsFetched:number; perRouteLimit:number; candidateLimit:number; truncated:boolean; durationMs:number; fullCartesianEvaluated:false; llmCalls:0; billedTokens:0;};
  warnings:string[];
}
export interface ReviewPacket {
  id:string; creatorId:string; createdAt:number; fingerprint:string; runId:string;
  candidates:number; characters:number; estimatedTokens:null;
  modelStatus:"not_called"; executable:false; executionBlocked:true; payload:Record<string,unknown>;
}
export type MatchingCommand =
  | {type:"recall"; query:RecallQuery}
  | {type:"prepare_review"; runId:string; creatorId:string}
  | {type:"demo_change"; productId:string; expectedRevision:number; change:"raise_price"|"lower_price"|"offer_unavailable"|"offer_available"};
export type MatchingResponse = {kind:"run";run:MatchRun}|{kind:"packet";packet:ReviewPacket}|{kind:"change";result:ImportResult;product:MatchProduct;message:string};
