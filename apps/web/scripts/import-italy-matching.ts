import {createHash} from "node:crypto";
import {readFileSync,writeFileSync,existsSync,mkdtempSync,rmSync,mkdirSync,realpathSync} from "node:fs";
import {dirname,resolve,join} from "node:path";
import {fileURLToPath} from "node:url";
import {MatchingStore} from "../src/server/matching/store.ts";
import {planItalyImport,type ItalySourceFile} from "../src/server/matching/italy-import.ts";
import {evaluateItalyReplay} from "../src/server/matching/italy-evaluation.ts";

const projectRoot=resolve(dirname(fileURLToPath(import.meta.url)),"../../..");
const legacyRoot=resolve(projectRoot,"../01-BDSystem-V2");
const varDir=resolve(projectRoot,"var"),dbPath=join(varDir,"matching-italy.sqlite");
if(process.argv.length>2)throw new Error("This fixed-scope offline importer takes no arguments.");
mkdirSync(varDir,{recursive:true,mode:0o700});
// A redirected var directory must never turn this importer into a writer of the old project.
if(!realpathSync(varDir).startsWith(realpathSync(projectRoot)+"/"))throw new Error("Output must remain inside the new project.");
if(existsSync(dbPath)&&!realpathSync(dbPath).startsWith(realpathSync(varDir)+"/"))throw new Error("Database path points outside the new project.");
const run="kd_d1fae274877443e3b76f13256055b657";
function source(relative:string,ref:string):ItalySourceFile {
  const bytes=readFileSync(resolve(legacyRoot,relative));
  return {ref,path:relative,sha256:createHash("sha256").update(bytes).digest("hex"),data:JSON.parse(bytes.toString("utf8"))};
}
const sources={
  pilot:source("data/research/product-exploration/it-global-sales-pilot-20260910.json","legacy:it-pilot-20260910"),
  request:source(`data/research/kalodata/${run}/request.json`,`legacy:${run}:request`),
  result:source(`data/research/kalodata/${run}/result.json`,`legacy:${run}`),
};
let store:MatchingStore|undefined;
let evaluationStore:MatchingStore|undefined;
let temporaryDirectory:string|undefined;
try {
  let importedAt=Date.now();
  if(existsSync(dbPath)) {store=new MatchingStore(dbPath,{seed:false});importedAt=store.stats().dataset.importedAt??importedAt;}
  const plan=planItalyImport(sources,importedAt);
  if(plan.report.counts.rejected)throw new Error(`Import rejected ${plan.report.counts.rejected} rows; no data was written. Inspect the adapter before accepting a partial cohort.`);
  if(store&&store.stats().dataset.id!==plan.dataset.id)throw new Error("Source fingerprints changed. Review a new dataset version; the existing offline database was preserved.");
  store??=new MatchingStore(dbPath,{seed:false,dataset:plan.dataset});
  const imported=store.upsert(plan.batch),repeated=store.upsert(plan.batch);
  if(repeated.inserted||repeated.updated)throw new Error("Import was not idempotent.");
  // Fresh replay DB isolates latency/cache checks from existing UI runs; never clear the user's DB.
  temporaryDirectory=mkdtempSync(join(varDir,"italy-replay-"));
  evaluationStore=new MatchingStore(join(temporaryDirectory,"replay.sqlite"),{seed:false,dataset:plan.dataset});
  evaluationStore.upsert(plan.batch);
  const evaluation=evaluateItalyReplay(evaluationStore,plan.batch);
  const report={...plan.report,evaluatedAt:Date.now(),imported,repeated,stats:store.stats(),evaluation};
  const save=(name:string,value:unknown)=>{
    const target=join(varDir,name);
    if(existsSync(target)&&!realpathSync(target).startsWith(realpathSync(varDir)+"/"))throw new Error("Output file points outside project storage.");
    writeFileSync(target,JSON.stringify(value,null,2)+"\n",{mode:0o600});
  };
  save("italy-offline-import-report.json",report);
  // Allowlisted local facts only. No original file copies, credentials, avatars, IM or contact fields.
  save("italy-offline-batch.json",{dataset:plan.dataset,batch:plan.batch});
  process.stdout.write(JSON.stringify({database:"var/matching-italy.sqlite",report:"var/italy-offline-import-report.json",counts:plan.report.counts,imported,repeated,evaluation},null,2)+"\n");
} finally {
  evaluationStore?.close();store?.close();
  if(temporaryDirectory)rmSync(temporaryDirectory,{recursive:true,force:true});
}
