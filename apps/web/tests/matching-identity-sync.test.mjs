import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,rmSync} from "node:fs";
import {join} from "node:path";
import {tmpdir} from "node:os";
import {MatchingStore} from "../src/server/matching/store.ts";
import {syncRegistryProfiles} from "../src/server/matching/identity-profile-sync.ts";
import {parseMatchingQuery} from "../src/server/matching/validation.ts";

test("a missing registry preserves the existing analysis store without creating or clearing a checkpoint",t=>{
  const dir=mkdtempSync(join(tmpdir(),"bdhub-sync-missing-"));
  const store=new MatchingStore(join(dir,"matching.sqlite"),{seed:false,dataset:{id:"italy-profiles-fixture",mode:"imported-offline",label:"fixture",importedAt:1000,sourceRefs:["fixture"],warnings:[]}});
  t.after(()=>{store.close();rmSync(dir,{recursive:true,force:true});});
  const before=store.stats(),checkpoint=store.profileSyncCheckpoint();
  const status=syncRegistryProfiles(store,join(dir,"does-not-exist.sqlite"));
  assert.equal(status.status,"not_imported");assert.deepEqual(store.stats(),before);assert.deepEqual(store.profileSyncCheckpoint(),checkpoint);
});
test("a source or recompute error never exposes diagnostics and retains the actually committed checkpoint",()=>{
  const committed={status:"ready",errorCode:null,revision:3,sourceCursor:20,lastAppliedAt:1000,registryCreators:1,syncedCreators:1,lastBatch:{events:1,considered:1,inserted:0,updated:1,unchanged:0,skipped:0},gaps:{categoryUnavailable:0,categoryHistorical:0,metricsPartial:0,periodUnknown:0},recomputedRuns:0,pendingRecomputes:1};
  const fake={getProfileSyncStatus:()=>committed,profileSyncCheckpoint(){throw Object.assign(new Error("private credential details"),{code:"untrusted_text"});}};
  const result=syncRegistryProfiles(fake,"unused");assert.equal(result.revision,3);assert.equal(result.sourceCursor,20);assert.equal(result.errorCode,"profile_sync_unavailable");assert(!JSON.stringify(result).includes("credential"));
});
test("registry deep links are confined to the Italy profile dataset and never use a handle lookup",()=>{
  const query=parseMatchingQuery("http://local/api?dataset=italy-profiles&view=linked_creator&registryCreatorId=creator_01234567890123456789012345678901");
  assert.equal(query.view,"linked_creator");assert.equal(query.registryCreatorId,"creator_01234567890123456789012345678901");
  for(const url of ["dataset=italy&view=linked_creator&registryCreatorId=creator_x","dataset=italy-profiles&view=creators&registryCreatorId=creator_x","dataset=italy-profiles&view=linked_creator&handle=name","dataset=italy-profiles&view=linked_creator&registryCreatorId=a&registryCreatorId=b"])assert.throws(()=>parseMatchingQuery("http://local/api?"+url));
});
