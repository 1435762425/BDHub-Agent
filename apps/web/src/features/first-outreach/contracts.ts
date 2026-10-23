export type FirstOutreachAction="prepare"|"sync"|"start"|"continue"|"stop"|"set-approvals"|"reconcile";
export type FirstOutreachRequest={action:FirstOutreachAction;market:"br";requestId:string;expectedRevision:number;approvalsEnabled?:boolean};
export type FirstOutreachState={
 schemaVersion:"bdhub.first-outreach.v1";market:"br";
 control:{revision:number;mode:"stopped"|"canary"|"continuous";approvalsEnabled:boolean;updatedAt:number};
 readiness:{ready:boolean;blockers:string[]};
 applicationCoverage:"unverified"|"complete";
 counts:{products:number;matchedCreators:number;sent:number;pendingApplications:number;approved:number;needsReview:number;unknown:number};
 candidates:{creatorId:string;handle:string;pid:string;productName:string;categoryName:string;gmv:string|null;message:string}[];
 applications:{id:string;handle:string;pid:string;productName:string;state:string;reason:string|null}[];
 runtime:{state:string;reason:string|null;updatedAt:number|null};
 platformWrites:0;sourceUpdatedAt?:number|null;
};
