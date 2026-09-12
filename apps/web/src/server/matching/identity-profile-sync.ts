import type {MatchingStore} from "./store.ts";
import type {MatchingProfileSyncStatus} from "../../features/matching/profile-sync-contracts.ts";
import {readRegistryProfileChanges,projectRegistryCreator} from "./identity-profile-projection.ts";

const recent=new WeakMap<MatchingStore,MatchingProfileSyncStatus>();
function empty():MatchingProfileSyncStatus{return {status:"not_imported",errorCode:null,revision:0,sourceCursor:0,lastAppliedAt:null,registryCreators:0,syncedCreators:0,lastBatch:{events:0,considered:0,inserted:0,updated:0,unchanged:0,skipped:0},gaps:{categoryUnavailable:0,categoryHistorical:0,metricsPartial:0,periodUnknown:0},recomputedRuns:0,pendingRecomputes:0};}

/** Local derived-index maintenance only; no network, models, messaging or old DB. */
export function syncRegistryProfiles(store:MatchingStore,identityPath:string):MatchingProfileSyncStatus {
  const previous=store.getProfileSyncStatus()??empty();
  try{
    const checkpoint=store.profileSyncCheckpoint();
    const changes=readRegistryProfileChanges(identityPath,checkpoint);
    if(changes.status==="not_imported"){
      const status={...previous,status:"not_imported" as const,errorCode:null};recent.set(store,status);return status;
    }
    const creators=[],mappings=[];
    const gaps={categoryUnavailable:0,categoryHistorical:0,metricsPartial:0,periodUnknown:0};let skipped=0;
    for(const record of changes.records){
      const projected=projectRegistryCreator(record,store.getCreatorByOec("it",record.oecId));
      if(projected.creator)creators.push(projected.creator);
      if(projected.mapping)mappings.push(projected.mapping);
      skipped+=projected.skipped;
      for(const key of Object.keys(gaps) as (keyof typeof gaps)[])gaps[key]+=projected.gaps[key];
    }
    store.syncProfiles({expectedRevision:checkpoint.revision,expectedCursor:checkpoint.cursor,
      sourceKey:changes.sourceKey!,cursor:changes.cursor,registryCreators:changes.registryCreators,
      events:changes.events,creators,mappings,skipped,gaps});
    store.drainProfileRecomputes(3);
    const status=store.getProfileSyncStatus()??empty();
    const result={...status,sourceHead:changes.sourceHead,pendingEvents:Math.max(0,changes.sourceHead-changes.cursor)};
    recent.set(store,result);return result;
  }catch(error){
    // Keep existing analysis data and its last successful checkpoint readable.
    // Ingest and checkpoint are atomic. A later recompute failure retains the
    // successful ingest checkpoint and the durable pending queries.
    const candidate=error&&typeof error==="object"&&"code" in error?String(error.code):"profile_sync_unavailable";
    const allowed=new Set(["profile_sync_source_changed","profile_sync_cursor_regressed","profile_sync_conflict","identity_schema_mismatch","profile_sync_identity_conflict","registry_source_changed","registry_cursor_rewound","registry_schema_mismatch","registry_profile_invalid"]);
    const result={...(store.getProfileSyncStatus()??previous),status:"error" as const,errorCode:allowed.has(candidate)?candidate:"profile_sync_unavailable"};
    recent.set(store,result);return result;
  }
}
export function registrySyncStatus(store:MatchingStore){return recent.get(store)??store.getProfileSyncStatus();}
