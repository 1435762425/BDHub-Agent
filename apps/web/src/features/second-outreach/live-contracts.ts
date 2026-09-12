export type LiveItemState="pending"|"creating_conversation"|"conversation_ready"|"submitting"|"accepted_candidate"|"confirmed"|"result_unknown"|"failed_not_sent"|"partial_delivery";
export type LiveComponentState="pending"|"inflight"|"accepted_candidate"|"confirmed"|"result_unknown"|"failed_not_sent";
export type LiveLaunchState="not_started"|"started"|"running"|"finished"|"start_unknown";
export interface SecondLiveStartRequest {trialId:string;snapshotHash:string;confirmed:true;requestId:string}
export interface LiveTrialComponent {componentId:string;componentKind:"card"|"text";position:number;state:LiveComponentState;productId:string|null;listId:string|null;listName:string|null;campaignName:string|null;confirmed:boolean;errorCode:string|null}
export interface LiveTrialAttempt {attemptId:string;stage:string;state:string;startedAt:string;finishedAt:string|null;errorCode:string|null}
export interface LiveTrialItem {
  itemId:string;opportunityId:string;creatorId:string;oecId:string;handle:string|null;draftId:string;
  textIt:string;translationZh:string;textSha256:string;state:LiveItemState;partialDelivery:boolean;requiresReconciliation:boolean;
  unknownStage:string|null;errorCode:string|null;components:LiveTrialComponent[];attempts:LiveTrialAttempt[];
}
export interface FrozenLiveTrial {
  trialId:string;snapshotHash:string;market:"it";account:"acc6";campaignId:"italy-second-pilot";createdAt:string;expiresAt:string;
  approved:boolean;approvedAt:string|null;paused:boolean;expired:boolean;complete:boolean;
  blockedByUnknown:{trialId:string;itemId:string}[];items:LiveTrialItem[];
}
export interface SecondLiveResponse {trial:FrozenLiveTrial;launch:{state:LiveLaunchState;pid:number|null;requestId:string|null}}
