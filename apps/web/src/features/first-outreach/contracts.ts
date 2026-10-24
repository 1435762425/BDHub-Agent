export type FirstOutreachAction="prepare"|"sync"|"start"|"continue"|"stop"|"set-approvals"|"reconcile";
export type FirstOutreachRequest={action:FirstOutreachAction;market:"br";requestId:string;expectedRevision:number;approvalsEnabled?:boolean};
export const FIRST_MATERIAL_GAP_REASONS=["commission_terms_unavailable","prior_intent_superseded","binding_not_current","localized_name_missing","material_preparation_pending","original_intent_pending"] as const;
export type FirstMaterialGapReason=(typeof FIRST_MATERIAL_GAP_REASONS)[number];
export type FirstOutreachMaterials={remaining:number;readyOffers:number;gapCounts:Record<FirstMaterialGapReason,number>;actionable:number;unchanged:number};
export type FirstOutreachCycle={state:"queued"|"running"|"waiting"|"complete"|"partial"|"stopped"|"idle"|"attention";reason:string|null;updatedAt:number|null;nextRunAt:number|null;materials?:FirstOutreachMaterials};
export type FirstOutreachState={
 schemaVersion:"bdhub.first-outreach.v1";market:"br";
 control:{revision:number;mode:"stopped"|"canary"|"continuous";approvalsEnabled:boolean;updatedAt:number};
 readiness:{ready:boolean;blockers:string[]};
 applicationCoverage:"unverified"|"complete";
 counts:{products:number;matchedCreators:number;sent:number;pendingApplications:number;approved:number;needsReview:number;unknown:number};
 candidates:{creatorId:string;handle:string;pid:string;productName:string;categoryName:string;gmv:string|null;message:string}[];
 applications:{id:string;handle:string;pid:string;productName:string;state:string;reason:string|null}[];
 runtime:{state:string;reason:string|null;updatedAt:number|null};
 automation?:{supply:FirstOutreachCycle;samples:FirstOutreachCycle};
 platformWrites:0;sourceUpdatedAt?:number|null;
};
