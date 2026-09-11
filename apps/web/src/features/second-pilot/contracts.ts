import type {FactSource} from "../matching/contracts.ts";

export type SecondPilotScenario = "accepted"|"receipt_lost"|"before_submit_crash";
export type SecondPilotLocalControl = "running"|"paused";
export type SecondPilotActionStatus = "queued"|"submitting"|"simulated_accepted"|"result_unknown"|"cancelled";
export interface SecondPilotProduct {
  productId:string; pid:string; title:string; italianName:string; units:number; source:FactSource;
}
export interface SecondPilotDraft {
  text:string; language:"it"; method:"deterministic"; skillVersion:string; claimRefs:string[];
}
export interface SecondPilotCaseInput {
  id:string; sourceFingerprint:string; creatorId:string;
  creatorRef:{namespace:"kalodata";id:string}; handle:string; market:"it";
  products:SecondPilotProduct[]; draft:SecondPilotDraft; liveBlockers:string[];
}
export interface SecondPilotSnapshot {
  id:string; caseId:string; createdAt:number; revision:number; localControlRevision:number;
  fingerprint:string; sourceFingerprint:string; draftHash:string;
  input:SecondPilotCaseInput;
  mode:"dry_run"; transport:"local-simulator"; liveExecutable:false;
  realIdentityStatus:"unverified"; realControl:"unknown"; realMarketingStopped:null;
}
export interface SecondPilotAction {
  id:string; caseId:string; snapshotId:string; createdAt:number;
  mode:"dry_run"; transport:"local-simulator"; componentKind:"text";
  scenario:SecondPilotScenario; status:SecondPilotActionStatus;
  attempts:number; fence:number; leaseOwner:string|null; leaseUntil:number|null;
  submittedAt:number|null; receiptRef:string|null; resolvedAt:number|null; note:string;
}
export interface SecondPilotCase extends SecondPilotCaseInput {
  revision:number; localControl:SecondPilotLocalControl; localControlRevision:number;
  updatedAt:number; realIdentityStatus:"unverified"; realControl:"unknown"; realMarketingStopped:null;
  snapshots:SecondPilotSnapshot[]; actions:SecondPilotAction[];
}
export interface SecondPilotOverview {
  mode:"dry_run"; transport:"local-simulator"; cases:number; products:number; edges:number;
  frozen:number; queued:number; submitting:number; simulatedAccepted:number;
  resultUnknown:number; cancelled:number; paused:number; attempts:number; simulatedReceipts:number;
  realSends:0; modelCalls:0;
  worker:{online:boolean;lastSeenAt:number|null};
}
export interface SecondPilotImportResult {inserted:number;updated:number;unchanged:number;}
