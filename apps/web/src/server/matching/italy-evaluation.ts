import assert from "node:assert/strict";
import {performance} from "node:perf_hooks";
import type {MatchingBatch, MatchRun, RecallQuery} from "../../features/matching/contracts.ts";
import type {MatchingStore} from "./store.ts";

function distribution(values:number[]) {
  assert(values.length>0,"Italy replay requires non-empty measurements.");
  const sorted=[...values].sort((a,b)=>a-b);
  const at=(p:number)=>Number(sorted[Math.max(0,Math.ceil(sorted.length*p)-1)].toFixed(3));
  return {min:Number(sorted[0].toFixed(3)),p50:at(.5),p95:at(.95),max:Number(sorted[sorted.length-1].toFixed(3))};
}

function sameSet(actual:string[],expected:Set<string>,scope:string) {
  const unique=new Set(actual);
  assert.equal(actual.length,unique.size,`${scope}: duplicate candidate pairs.`);
  assert.equal(unique.size,expected.size,`${scope}: candidate count differs from observed positive pairs.`);
  assert([...unique].every(id=>expected.has(id)),`${scope}: unexpected candidate pair.`);
}

/** Replays known observations in an isolated, newly imported store; this is not a relevance/holdout evaluation. */
export function evaluateItalyReplay(store:MatchingStore,batch:MatchingBatch) {
  const products=batch.products??[],creators=batch.creators??[],evidence=batch.evidence??[];
  assert(products.length>0&&creators.length>0,"Italy replay requires products and creators.");
  assert(products.every(p=>p.market==="it")&&creators.every(c=>c.market==="it"),"Italy replay only accepts an Italy dataset.");
  const initialStats=store.stats();
  assert.equal(initialStats.mode,"imported-offline","Italy replay requires a separate offline-import dataset.");
  assert.equal(initialStats.products,products.length,"Store product count differs from the replay input.");
  assert.equal(initialStats.creators,creators.length,"Store creator count differs from the replay input.");
  assert.equal(new Set(products.map(p=>p.id)).size,products.length,"Duplicate product identities in replay input.");
  assert.equal(new Set(products.map(p=>p.pid)).size,products.length,"Duplicate product PIDs in replay input.");
  assert.equal(new Set(creators.map(c=>c.id)).size,creators.length,"Duplicate creator identities in replay input.");
  const productIdsByPid=new Map(products.map(p=>[p.pid,p.id]));
  const creatorIds=new Set(creators.map(c=>c.id));
  const byProduct=new Map(products.map(p=>[p.id,new Set<string>()]));
  const byCreator=new Map(creators.map(c=>[c.id,new Set<string>()]));
  let positiveObservations=0;
  for(const edge of evidence) {
    if(edge.units<=0||edge.market!=="it")continue;
    const productId=productIdsByPid.get(edge.pid);
    assert(productId&&creatorIds.has(edge.creatorId),"Positive Italy evidence references an entity absent from replay input.");
    byProduct.get(productId)!.add(edge.creatorId);
    byCreator.get(edge.creatorId)!.add(productId);
    positiveObservations++;
  }
  assert(positiveObservations>0,"Italy replay requires positive exact-PID observations.");
  assert([...byCreator.values()].every(ids=>ids.size>0),"Every replay creator must have a positive exact-PID observation.");
  assert([...byProduct.values(),...byCreator.values()].every(ids=>ids.size<=50),"Replay exceeds the 50-candidate exact comparison boundary.");
  const uniquePairs=[...byProduct.values()].reduce((total,ids)=>total+ids.size,0);
  const queries:RecallQuery[]=[
    ...products.map(p=>({direction:"product" as const,subjectId:p.id,source:"second" as const,limit:50})),
    ...creators.map(c=>({direction:"creator" as const,subjectId:c.id,source:"second" as const,limit:50})),
  ];
  const uncachedMs:number[]=[],cachedMs:number[]=[],creatorRuns=new Map<string,MatchRun>();
  const productResults:{pid:string;title:string;expectedPairs:number;returnedPairs:number}[]=[];
  let productReturnedPairs=0,creatorReturnedPairs=0,maxRowsFetched=0;
  for(const query of queries) {
    let started=performance.now();
    const run=store.recall(query);
    uncachedMs.push(performance.now()-started);
    assert.equal(run.cacheHit,false,"Replay must start in a newly imported store with no existing recall cache.");
    assert.equal(run.market,"it","Recall crossed the Italy market boundary.");
    assert.equal(run.diagnostics.llmCalls,0,"Replay unexpectedly called a model.");
    assert.equal(run.diagnostics.billedTokens,0,"Replay unexpectedly incurred tokens.");
    assert.equal(run.diagnostics.fullCartesianEvaluated,false,"Replay unexpectedly evaluated the full Cartesian product.");
    assert.equal(run.diagnostics.truncated,false,"Exact replay unexpectedly truncated known evidence pairs.");
    assert(run.candidates.every(c=>c.product.market==="it"&&c.creator.market==="it"&&c.sources.includes("exact_pid")&&c.features.exactUnits!==null&&c.features.exactUnits>0),"Second-source recall returned a candidate without same-market positive exact-PID evidence.");
    const productDirection=query.direction==="product";
    const expected=(productDirection?byProduct:byCreator).get(query.subjectId)!;
    sameSet(run.candidates.map(c=>productDirection?c.creator.id:c.product.id),expected,"Second-source replay");
    assert(run.candidates.every(c=>(productDirection?c.product.id:c.creator.id)===query.subjectId),"Recall returned a pair unrelated to its subject.");
    maxRowsFetched=Math.max(maxRowsFetched,run.diagnostics.rowsFetched);
    if(productDirection) {
      const product=products.find(p=>p.id===query.subjectId)!;
      productResults.push({pid:product.pid,title:product.title,expectedPairs:expected.size,returnedPairs:run.candidates.length});
      productReturnedPairs+=run.candidates.length;
    } else {
      creatorRuns.set(query.subjectId,run);
      creatorReturnedPairs+=run.candidates.length;
    }
    started=performance.now();
    const cached=store.recall(query);
    cachedMs.push(performance.now()-started);
    assert.equal(cached.cacheHit,true,"An unchanged replay query did not hit its result cache.");
    assert(cached.id===run.id&&cached.fingerprint===run.fingerprint,"Cached replay did not reuse the same run.");
    sameSet(cached.candidates.map(c=>productDirection?c.creator.id:c.product.id),expected,"Cached second-source replay");
  }
  assert.equal(productReturnedPairs,uniquePairs,"Product replay did not cover all known evidence pairs.");
  assert.equal(creatorReturnedPairs,uniquePairs,"Creator replay did not cover all known evidence pairs.");

  const packetCharacters:number[]=[],candidateHistogram:Record<string,number>={};
  for(const creator of creators) {
    const run=creatorRuns.get(creator.id)!;
    const packet=store.prepareReview(run.id,creator.id);
    assert.equal(packet.modelStatus,"not_called","Replay packet unexpectedly called a model.");
    assert.equal(packet.estimatedTokens,null,"Replay must not infer tokens from characters.");
    assert.equal(packet.executable,false,"Replay packet must never be executable.");
    assert.equal(packet.executionBlocked,true,"Offline replay must preserve the execution block.");
    assert.equal(packet.characters,JSON.stringify(packet.payload).length,"Reported packet characters differ from its actual payload.");
    assert(packet.characters>0&&packet.characters<=6000,"Review packet exceeds its character budget.");
    assert(packet.candidates>0&&packet.candidates<=5&&packet.candidates<=run.candidates.length,"Review packet exceeds its candidate boundary.");
    // This fixture has at most two known products per creator, so neither should disappear in the packet.
    if(run.candidates.length<=2)assert.equal(packet.candidates,run.candidates.length,"A small creator review omitted an observed product.");
    assert(Array.isArray(packet.payload.candidates),"Review packet candidates are missing.");
    assert.equal(packet.payload.candidates.length,packet.candidates,"Review packet candidate count differs from its payload.");
    packetCharacters.push(packet.characters);
    candidateHistogram[String(packet.candidates)]=(candidateHistogram[String(packet.candidates)]??0)+1;
  }

  const missingProductCategories=products.filter(p=>!p.categories.length).length;
  const missingCreatorCategories=creators.filter(c=>!c.categories.length).length;
  const missingCreatorPriceBands=creators.filter(c=>c.priceMinMinor===null||c.priceMaxMinor===null).length;
  const firstSourceCounts:Record<string,number>={};
  let firstCandidates=0,knownPairEnrichments=0,firstTruncatedQueries=0;
  for(const secondQuery of queries) {
    const run=store.recall({...secondQuery,source:"first"});
    firstCandidates+=run.candidates.length;
    if(run.diagnostics.truncated)firstTruncatedQueries++;
    for(const candidate of run.candidates) {
      for(const source of candidate.sources) {
        // candidate() enriches a first-source result with known evidence; this is not a second retrieval route.
        if(source==="exact_pid")knownPairEnrichments++;
        else firstSourceCounts[source]=(firstSourceCounts[source]??0)+1;
      }
      if(missingProductCategories===products.length&&missingCreatorCategories===creators.length&&!(batch.demands??[]).length) {
        assert(candidate.sources.includes("cold_start")&&candidate.sources.every(source=>source==="cold_start"||source==="exact_pid"),"Profile-free first-source replay unexpectedly claims profile-based matching.");
      }
    }
  }
  const stats=store.stats();
  assert.equal(stats.llmCalls,0,"Offline store reports unexpected model usage.");
  assert.equal(stats.billedTokens,0,"Offline store reports unexpected token usage.");
  return {
    evaluation:"observed_exact_pid_replay" as const,
    scope:"Replays already observed positive Italy PID pairs in both directions. It is not recommendation quality, a holdout evaluation, current identity verification, or sending readiness.",
    products:products.length,creators:creators.length,positiveObservations,uniquePairs,
    second:{productQueries:products.length,creatorQueries:creators.length,productReturnedPairs,creatorReturnedPairs,missingPairs:0,unexpectedPairs:0,duplicatePairs:0,byProduct:productResults},
    timing:{unit:"milliseconds",scope:"Full synchronous recall call; uncached means application result-cache miss, not cold disk or OS cache. First-source probes and review packets are excluded.",uncached:{queries:uncachedMs.length,...distribution(uncachedMs)},cached:{queries:cachedMs.length,cacheHits:cachedMs.length,...distribution(cachedMs)},maxRowsFetched},
    reviewPackets:{count:packetCharacters.length,characters:distribution(packetCharacters),candidateHistogram,estimatedTokens:null,modelCalls:0,executable:false,executionBlocked:true},
    firstQuality:missingProductCategories||missingCreatorCategories||missingCreatorPriceBands?"not_evaluated_missing_profiles" as const:"not_evaluated_no_reference_judgments" as const,
    first:{queries:queries.length,returnedPairs:firstCandidates,sourceCounts:firstSourceCounts,knownPairEnrichments,truncatedQueries:firstTruncatedQueries,missingProductCategories,missingCreatorCategories,missingCreatorPriceBands,note:"Cold-start candidates are suggestions to obtain missing facts, not evidence of creator-product suitability. Exact-PID enrichment describes already known pair facts, not the first-source retrieval route."},
    modelCalls:0,billedTokens:0,estimatedTokens:null,realSends:0,
  };
}
