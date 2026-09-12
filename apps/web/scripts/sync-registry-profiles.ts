import {fileURLToPath} from "node:url";
import {dirname,resolve} from "node:path";
import {existsSync,writeFileSync} from "node:fs";
import {DatabaseSync} from "node:sqlite";
import {MatchingStore} from "../src/server/matching/store.ts";
import {syncRegistryProfiles} from "../src/server/matching/identity-profile-sync.ts";

const root=resolve(dirname(fileURLToPath(import.meta.url)),"../../.."),varDir=resolve(root,"var");
const matchingPath=resolve(varDir,"matching-italy-profiles.sqlite"),identityPath=resolve(varDir,"creator-identities.sqlite");
if(process.argv.slice(2).some(value=>value!=="--verify"))throw new Error("Only --verify is supported.");
if(!existsSync(matchingPath))throw new Error("The initial Italy profile dataset must already exist.");
const store=new MatchingStore(matchingPath,{seed:false});
try{
  if(!store.stats().dataset.id.startsWith("italy-profiles-")||store.stats().mode!=="imported-offline")throw new Error("Not the Italy profile dataset.");
  let status=syncRegistryProfiles(store,identityPath);
  if(status.status!=="ready")throw new Error(status.errorCode||status.status);
  for(let step=0;step<100&&(status.pendingEvents||status.pendingRecomputes);step++){
    status=syncRegistryProfiles(store,identityPath);if(status.status!=="ready")throw new Error(status.errorCode||status.status);
  }
  if(status.pendingEvents||status.pendingRecomputes)throw new Error("Bounded sync did not catch up; invoke again with the stored checkpoint.");
  const first=store.stats(),checkpoint=store.profileSyncCheckpoint();
  const second=syncRegistryProfiles(store,identityPath);
  if(JSON.stringify(checkpoint)!==JSON.stringify(store.profileSyncCheckpoint())||first.semanticBuilds!==store.stats().semanticBuilds)throw new Error("No-change sync is not idempotent.");
  const result:Record<string,unknown>={schema:"bdhub.registry-profile-sync-run.v1",profileSync:second,matchingCreators:first.creators,matchingProducts:first.products,noChangeSyncIdempotent:true,modelCalls:0,billedTokens:0,realSends:0};
  if(process.argv.includes("--verify")){
    const reader=new DatabaseSync(identityPath,{readOnly:true});
    const ids=reader.prepare("SELECT creator_id FROM creator_identity WHERE market='it' ORDER BY creator_id").all().map(row=>String(row.creator_id));reader.close();
    const products=store.listProducts({market:"it",limit:100}).items;
    let creatorQueries=0,productQueries=0,packets=0,maxPacketCharacters=0,creatorPairs=0;
    const linked=new Set<string>();
    for(const identity of ids){
      const creator=store.resolveRegistryCreator(identity);if(!creator||creator.profileOrigin?.creatorId!==identity)throw new Error("Missing exact stable identity projection.");
      if(linked.has(creator.id))throw new Error("Two registry identities mapped to one matching creator.");linked.add(creator.id);
      const query={direction:"creator" as const,subjectId:creator.id,source:"first" as const,limit:50};
      const run=store.recall(query),again=store.recall(query);creatorQueries++;creatorPairs+=run.candidates.length;
      if(again.id!==run.id||!again.cacheHit||run.diagnostics.fullCartesianEvaluated||run.diagnostics.llmCalls)throw new Error("Recall cache or bounded analysis contract failed.");
      if(run.candidates.length&&!run.candidates.some(candidate=>candidate.readiness==="suppressed")){
        const packet=store.prepareReview(run.id,creator.id);packets++;maxPacketCharacters=Math.max(maxPacketCharacters,packet.characters);
        if(packet.characters>6000||packet.executable||packet.payload.creator===undefined)throw new Error("Review packet boundary failed.");
      }
    }
    for(const product of products){const query={direction:"product" as const,subjectId:product.id,source:"first" as const,limit:50};const run=store.recall(query);productQueries++;if(store.recall(query).id!==run.id)throw new Error("Product cache not idempotent.");}
    result.verification={linkedCreators:linked.size,creatorQueries,productQueries,creatorPairs,packets,maxPacketCharacters};
  }
  writeFileSync(resolve(varDir,"registry-profile-sync-run-report.json"),JSON.stringify(result,null,2)+"\n",{mode:0o600});
  console.log(JSON.stringify(result));
}finally{store.close();}
