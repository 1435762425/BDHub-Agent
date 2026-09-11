import {mkdtempSync,rmSync,writeFileSync,mkdirSync} from "node:fs";
import {tmpdir} from "node:os";
import {dirname,join,resolve} from "node:path";
import {performance} from "node:perf_hooks";
import {MatchingStore} from "../src/server/matching/store.ts";
import {makeMatchingFixture} from "../src/server/matching/fixtures.ts";
import type {RecallQuery} from "../src/features/matching/contracts.ts";

const args=process.argv.slice(2);
let large=false,output:string|undefined;
for(let i=0;i<args.length;i++){
  if(args[i]==="--large")large=true;
  else if(args[i]==="--out"&&args[i+1])output=resolve(args[++i]);
  else throw new Error(`Unknown benchmark argument: ${args[i]}`);
}
function percentile(values:number[],p:number){const sorted=[...values].sort((a,b)=>a-b);return Number(sorted[Math.min(sorted.length-1,Math.ceil(sorted.length*p)-1)].toFixed(3));}
const sizes=large?[[1000,10000],[10000,50000]]:[[1000,10000]];
const reports=[];
for(const [products,creators] of sizes){
  const directory=mkdtempSync(join(tmpdir(),"bdhub-matching-benchmark-"));
  let store:MatchingStore|undefined;
  try {
    const now=Date.now();
    store=new MatchingStore(join(directory,"matching.sqlite"),{seed:false,now:()=>now});
    const start=performance.now();
    const batch=makeMatchingFixture({products,creators,now});
    const imported=store.upsert(batch);
    const buildMs=performance.now()-start;
    const samples=[{market:"mx" as const,limit:7},{market:"br" as const,limit:7},{market:"it" as const,limit:6}];
    const subjects=samples.flatMap(sample=>[...store!.listProducts(sample).items.map(x=>({direction:"product" as const,id:x.id,market:x.market})),...store!.listCreators(sample).items.map(x=>({direction:"creator" as const,id:x.id,market:x.market}))]);
    const cold:number[]=[],warm:number[]=[],rows:number[]=[],counts:number[]=[];
    let hits=0;
    for(const subject of subjects){
      const query:RecallQuery={direction:subject.direction,subjectId:subject.id,source:"all",limit:20};
      let t=performance.now();const run=store.recall(query);cold.push(performance.now()-t);
      rows.push(run.diagnostics.rowsFetched);counts.push(run.candidates.length);
      if(run.candidates.length>20||run.diagnostics.llmCalls!==0||run.diagnostics.billedTokens!==0)throw new Error("Recall violated bounds or zero-LLM contract");
      t=performance.now();const cached=store.recall(query);warm.push(performance.now()-t);if(cached.cacheHit)hits++;
      if(cached.id!==run.id)throw new Error("Unchanged recall did not reuse run");
    }
    const stats=store.stats();
    const example:RecallQuery={direction:"product",subjectId:subjects.find(s=>s.direction==="product")!.id,source:"all",limit:20};
    reports.push({products:stats.products,creators:stats.creators,offers:stats.offers,evidence:stats.evidence,imported,buildMs:Number(buildMs.toFixed(2)),queries:cold.length,queryMarkets:subjects.reduce<Record<string,number>>((counts,s)=>{counts[s.market]=(counts[s.market]??0)+1;return counts;},{}),coldP50Ms:percentile(cold,.5),coldP95Ms:percentile(cold,.95),warmP50Ms:percentile(warm,.5),warmP95Ms:percentile(warm,.95),cacheHits:hits,maxRowsFetched:Math.max(...rows),maxReturned:Math.max(...counts),processRssMiB:Number((process.memoryUsage().rss/1024/1024).toFixed(1)),llmCalls:stats.llmCalls,billedTokens:stats.billedTokens,queryPlan:store.explainRecall(example)});
  }finally{store?.close();rmSync(directory,{recursive:true});}
}
const result={measuredAt:new Date().toISOString(),node:process.version,platform:process.platform,architecture:process.arch,scope:"Synthetic local SQLite benchmark, 40 product/creator queries stratified across MX/BR/IT per size; no real data, model or platform call; RSS is current whole-process RSS, not peak or exclusive memory. Cold means application-result cache miss, not cold disk/OS cache.",reports};
if(output){mkdirSync(dirname(output),{recursive:true});writeFileSync(output,JSON.stringify(result,null,2)+"\n");}
console.log(JSON.stringify(result,null,2));
