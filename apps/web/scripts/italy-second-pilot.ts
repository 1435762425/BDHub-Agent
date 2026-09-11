import {createHash} from "node:crypto";
import {readFileSync,writeFileSync,mkdtempSync,rmSync,mkdirSync,existsSync,realpathSync} from "node:fs";
import {dirname,resolve,join} from "node:path";
import {fileURLToPath} from "node:url";
import {buildItalySecondCases} from "../src/server/second-pilot/source.ts";
import {SecondPilotStore} from "../src/server/second-pilot/store.ts";
import {evaluateSecondPilot} from "../src/server/second-pilot/evaluate.ts";
import {rehearsalDecision} from "../src/server/second-pilot/rehearsal-plan.ts";
import type {SecondPilotScenario} from "../src/features/second-pilot/contracts.ts";

const root=resolve(dirname(fileURLToPath(import.meta.url)),"../../.."),directory=join(root,"var"),database=join(directory,"second-italy.sqlite");
const args=process.argv.slice(2);if(args.some(a=>a!=="--rehearse")||args.length>1)throw new Error("Only --rehearse (local simulator) is supported.");
mkdirSync(directory,{recursive:true,mode:0o700});if(!realpathSync(directory).startsWith(realpathSync(root)+"/"))throw new Error("Output must stay inside BDHub-Agent.");
function guarded(file:string){if(existsSync(file)&&!realpathSync(file).startsWith(realpathSync(directory)+"/"))throw new Error("Output is outside project storage.");return file;}
const source=JSON.parse(readFileSync(join(directory,"italy-offline-batch.json"),"utf8"));
const readiness=JSON.parse(readFileSync(join(directory,"italy-second-readiness-20260912.json"),"utf8"));
if(readiness.market!=="it"||readiness.liveEligible!==false||readiness.provenance?.transactionReadOnly!==true)throw new Error("Read-only Italy readiness observations are required.");
function canonical(value:unknown):string{if(Array.isArray(value))return `[${value.map(canonical).join(",")}]`;if(value&&typeof value==="object")return `{${Object.entries(value).sort(([a],[b])=>a<b?-1:a>b?1:0).map(([k,v])=>`${JSON.stringify(k)}:${canonical(v)}`).join(",")}}`;return JSON.stringify(value);}
for(const key of ["records","offerObservations","cardObservations","sourceCoverage"]){if(createHash("sha256").update(canonical(readiness[key])).digest("hex")!==readiness.provenance[`${key}Sha256`])throw new Error("Readiness source content differs from its recorded fingerprint.");}
const readinessFingerprint=createHash("sha256").update(JSON.stringify([readiness.provenance.recordsSha256,readiness.provenance.offerObservationsSha256,readiness.provenance.cardObservationsSha256,readiness.marketCapabilityObservation.sourceSha256])).digest("hex");
const cases=buildItalySecondCases(source.batch,source.dataset.id,readinessFingerprint);
if(cases.length!==readiness.records.length||!cases.every(c=>readiness.records.some((r:{externalId:string;verifiedOec:null})=>r.externalId===c.creatorRef.id&&r.verifiedOec===null)))throw new Error("Readiness cohort differs from the exact-PID source.");
const temporary=mkdtempSync(join(directory,"second-pilot-test-"));let store:SecondPilotStore|undefined;
try{
  const tests=await evaluateSecondPilot(cases,temporary);
  store=new SecondPilotStore(guarded(database),{leaseMs:1000});const imported=store.importCases(cases),repeat=store.importCases(cases);
  const dispatch={newlyQueued:0,skipped:{paused:0,completed:0,unknown:0,already_queued:0}};
  if(args.includes("--rehearse")){
    for(const [index,input] of cases.entries()){
      const current=store.get(input.id),decision=rehearsalDecision(current);if(decision.kind==="skip"){dispatch.skipped[decision.reason]++;continue;}
      const snapshot=store.freeze(input.id,current.revision,decision.freezeRequestId);
      const scenario:SecondPilotScenario=index===cases.length-1?"before_submit_crash":index===cases.length-2?"receipt_lost":"accepted";
      store.queue(snapshot.id,scenario,`rehearsal-queue:${snapshot.id}`);dispatch.newlyQueued++;
    }
    for(let steps=0;steps<cases.length*2;steps++)if(!await store.tick("second-initial-rehearsal"))break;
    if(store.overview().submitting){await new Promise(resolve=>setTimeout(resolve,1100));for(let steps=0;steps<cases.length;steps++)if(!await store.tick("second-initial-rehearsal"))break;}
    // Verify only existing simulator receipts; a pre-submit interruption stays unknown.
    for(const input of cases)for(const action of store.get(input.id).actions)if(action.status==="result_unknown")store.verify(action.id,`initial-verify:${input.id}`);
  }
  const observations={profiles:readiness.records.length,historicalIdentityHints:readiness.records.filter((r:{localOecHint:unknown})=>r.localOecHint!==null).length,verifiedOec:readiness.records.filter((r:{verifiedOec:unknown})=>r.verifiedOec!==null).length,historicalOffers:readiness.offerObservations.length,historicalCards:readiness.cardObservations.length,sendCapability:readiness.marketCapabilityObservation.statuses.send,localItCoverage:readiness.sourceCoverage.tables,identitySemantics:readiness.identitySemantics,sourceRefs:readiness.provenance.sources};
  const report={schema:"bdhub.italy-second-pilot.v1",at:Date.now(),datasetId:source.dataset.id,readinessFingerprint,observations,tests,imported,repeat,dispatch,persistent:store.overview(),mode:"dry_run",transport:"local-simulator",realSends:0,liveAuthorized:false};
  writeFileSync(guarded(join(directory,"italy-second-pilot-report.json")),JSON.stringify(report,null,2)+"\n",{mode:0o600});
  process.stdout.write(JSON.stringify({status:tests.status,cases:cases.length,edges:tests.productEdges,drafts:tests.drafts,observations:{identities:observations.verifiedOec,hints:observations.historicalIdentityHints,offers:observations.historicalOffers,cards:observations.historicalCards,sendCapability:observations.sendCapability},rehearsal:store.overview(),report:"var/italy-second-pilot-report.json"},null,2)+"\n");
}finally{store?.close();rmSync(temporary,{recursive:true,force:true});}
