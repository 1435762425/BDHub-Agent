export type ProfileMetricState="absent"|"no_value"|"unauthorized"|"error"|"zero"|"value";
export interface MatchingProfileOrigin {
  kind:"identity_registry";creatorId:string;observationRef:string;observedAt:number;
  categoryMode:"observed"|"historical_fallback"|"unavailable"|"conflict_preserved";
  metricStates:{followers:ProfileMetricState;unitsSold:ProfileMetricState;avgViews:ProfileMetricState;gmvValue:ProfileMetricState};
}
export interface MatchingProfileSyncStatus {
  status:"ready"|"not_imported"|"error";errorCode:string|null;
  revision:number;sourceCursor:number;lastAppliedAt:number|null;
  registryCreators:number;syncedCreators:number;
  lastBatch:{events:number;considered:number;inserted:number;updated:number;unchanged:number;skipped:number};
  gaps:{categoryUnavailable:number;categoryHistorical:number;metricsPartial:number;periodUnknown:number};
  recomputedRuns:number;pendingRecomputes:number;
  sourceHead?:number;pendingEvents?:number;
}
