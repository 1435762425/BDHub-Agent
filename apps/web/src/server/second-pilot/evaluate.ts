import assert from "node:assert/strict";
import {join} from "node:path";
import type {SecondPilotCaseInput,SecondPilotScenario} from "../../features/second-pilot/contracts.ts";
import {SecondPilotStore} from "./store.ts";

/** Three isolated local transport runs across the entire real-source cohort. No platform connection. */
export async function evaluateSecondPilot(inputs:SecondPilotCaseInput[],directory:string){
  const reports=[];
  for(const scenario of ["accepted","receipt_lost","before_submit_crash"] as SecondPilotScenario[]){
    let now=Date.now();const file=join(directory,`${scenario}.sqlite`);let store=new SecondPilotStore(file,{now:()=>now,leaseMs:100});
    try{
      assert.equal(store.importCases(inputs).inserted,inputs.length);assert.equal(store.importCases(inputs).unchanged,inputs.length);
      const pairs=inputs.reduce((n,c)=>n+c.products.length,0),actions: {caseId:string;snapshotId:string;actionId:string}[]=[];
      for(const input of inputs){
        assert(!/%|commission|gratuit|campion|spedizion|ho visto|hai già|garanti/i.test(input.draft.text),"Draft invented unavailable commercial terms or observed content.");
        for(const product of input.products)assert(input.draft.text.includes(product.italianName),"A merged draft lost a product.");
        const snapshot=store.freeze(input.id,1,`freeze:${input.id}`);
        assert.equal(snapshot.mode,"dry_run");assert.equal(snapshot.liveExecutable,false);assert.equal(snapshot.realMarketingStopped,null);
        const action=store.queue(snapshot.id,scenario,`queue:${input.id}`);
        assert.equal(store.queue(snapshot.id,scenario,`repeat-queue:${input.id}`).id,action.id);
        actions.push({caseId:input.id,snapshotId:snapshot.id,actionId:action.id});
      }
      for(let i=0;i<inputs.length;i++)assert.equal(await store.tick("full-cohort-a"),true);
      const beforeRestart=store.overview();store.close();now+=101;store=new SecondPilotStore(file,{now:()=>now,leaseMs:100});
      if(scenario==="before_submit_crash")for(let i=0;i<inputs.length;i++)assert.equal(await store.tick("full-cohort-b"),true);
      const beforeVerify=store.overview();assert.equal(beforeVerify.attempts,inputs.length);
      if(scenario==="accepted")assert.equal(beforeVerify.simulatedAccepted,inputs.length);
      else assert.equal(beforeVerify.resultUnknown,inputs.length);
      for(const ref of actions){
        const result=store.verify(ref.actionId,`verify:${ref.caseId}`);
        assert.equal(result.status,scenario==="before_submit_crash"?"result_unknown":"simulated_accepted");
        assert.equal(result.attempts,1);
        assert.equal(store.verify(ref.actionId,`verify:${ref.caseId}`).id,result.id);
      }
      assert.equal(await store.tick("full-cohort-b"),false);
      const after=store.overview();assert.equal(after.attempts,inputs.length);assert.equal(after.edges,pairs);assert.equal(after.realSends,0);assert.equal(after.modelCalls,0);
      assert.equal(after.simulatedReceipts,scenario==="before_submit_crash"?0:inputs.length);
      assert.equal(store.importCases(inputs).unchanged,inputs.length);
      reports.push({scenario,cases:inputs.length,edges:pairs,beforeRestart:{simulatedAccepted:beforeRestart.simulatedAccepted,submitting:beforeRestart.submitting,resultUnknown:beforeRestart.resultUnknown},beforeVerify:{simulatedAccepted:beforeVerify.simulatedAccepted,resultUnknown:beforeVerify.resultUnknown},afterVerify:{simulatedAccepted:after.simulatedAccepted,resultUnknown:after.resultUnknown},attempts:after.attempts,simulatedReceipts:after.simulatedReceipts,duplicateSubmissions:0,realSends:0});
    }finally{store.close();}
  }
  const lengths=inputs.map(c=>c.draft.text.length).sort((a,b)=>a-b);
  return {status:"passed",scope:"All cases exercised in separate local simulator databases; simulated receipt is not real delivery.",cases:inputs.length,productEdges:inputs.reduce((n,c)=>n+c.products.length,0),multiProductCases:inputs.filter(c=>c.products.length>1).length,drafts:{count:inputs.length,minCharacters:lengths[0],maxCharacters:lengths.at(-1),method:"deterministic",commercialPromises:false},scenarios:reports,modelCalls:0,realSends:0};
}
