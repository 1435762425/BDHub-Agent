import {createHash} from "node:crypto";
import {readFileSync,writeFileSync,existsSync,mkdirSync,mkdtempSync,rmSync,realpathSync} from "node:fs";
import {dirname,resolve,join} from "node:path";
import {fileURLToPath} from "node:url";
import {MatchingStore} from "../src/server/matching/store.ts";
import {planItalyProfileImport} from "../src/server/matching/italy-profile-import.ts";
import {evaluateItalyProfiles} from "../src/server/matching/italy-profile-evaluation.ts";
import type {ItalySourceFile} from "../src/server/matching/italy-import.ts";

if(process.argv.length>2)throw new Error("This fixed-scope importer takes no arguments.");
const root=resolve(dirname(fileURLToPath(import.meta.url)),"../../.."),legacy=resolve(root,"../01-BDSystem-V2"),directory=join(root,"var"),database=join(directory,"matching-italy-profiles.sqlite");
mkdirSync(directory,{recursive:true,mode:0o700});
if(!realpathSync(directory).startsWith(realpathSync(root)+"/"))throw new Error("Output must stay inside BDHub-Agent.");
function guarded(path:string){if(existsSync(path)&&!realpathSync(path).startsWith(realpathSync(directory)+"/"))throw new Error("Output file points outside project storage.");return path;}
function file(path:string,ref:string):ItalySourceFile {const bytes=readFileSync(join(legacy,path));return {path,ref,sha256:createHash("sha256").update(bytes).digest("hex"),data:JSON.parse(bytes.toString("utf8"))};}
const sources={
  pilot:file("data/research/product-exploration/it-global-sales-pilot-20260910.json","legacy:it-pilot-20260910"),
  products:file("data/research/product-exploration/pe_c0e628110f1a42bc9d9477ff2dec464f.json","legacy:it-category-20260910"),
  creators:file("data/research/outreach-ai/runs/oa_f9a4f4af84f3142fc119664a82de4be5/creator-snapshot.json","legacy:it-profile-20260730"),
};
let store:MatchingStore|undefined,evaluator:MatchingStore|undefined,temporary:string|undefined;
try {
  if(existsSync(database))store=new MatchingStore(guarded(database),{seed:false});
  const plan=planItalyProfileImport(sources,store?.stats().dataset.importedAt??Date.now());
  if(store&&store.stats().dataset.id!==plan.dataset.id)throw new Error("Source or alignment version changed. Existing data and assessments were preserved; review a new dataset version first.");
  store??=new MatchingStore(database,{seed:false,dataset:plan.dataset});
  const imported=store.upsert(plan.batch),repeat=store.upsert(plan.batch);
  if(repeat.inserted||repeat.updated)throw new Error("Profile import was not idempotent.");
  temporary=mkdtempSync(join(directory,"italy-profile-eval-"));
  evaluator=new MatchingStore(join(temporary,"matching.sqlite"),{seed:false,dataset:plan.dataset});evaluator.upsert(plan.batch);
  const evaluation=evaluateItalyProfiles(evaluator,plan.batch);
  const report={...plan.report,evaluatedAt:Date.now(),imported,repeat,stats:store.stats(),evaluation};
  writeFileSync(guarded(join(directory,"italy-profile-import-report.json")),JSON.stringify(report,null,2)+"\n",{mode:0o600});
  writeFileSync(guarded(join(directory,"italy-profile-batch.json")),JSON.stringify({dataset:plan.dataset,batch:plan.batch})+"\n",{mode:0o600});
  process.stdout.write(JSON.stringify({database:"var/matching-italy-profiles.sqlite",report:"var/italy-profile-import-report.json",counts:plan.report.counts,repeat,evaluation},null,2)+"\n");
} finally {evaluator?.close();store?.close();if(temporary)rmSync(temporary,{recursive:true,force:true});}
