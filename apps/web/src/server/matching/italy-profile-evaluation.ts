import assert from "node:assert/strict";
import {performance} from "node:perf_hooks";
import type {CreatorInput, MatchCandidate, MatchingBatch, MatchRun, ProductInput, RecallQuery} from "../../features/matching/contracts.ts";
import type {CategoryFact} from "../../features/matching/category-facts.ts";
import type {MatchingStore} from "./store.ts";

function distribution(values:number[]) {
  assert(values.length>0,"Italy profile evaluation requires non-empty measurements.");
  const sorted=[...values].sort((a,b)=>a-b);
  const at=(p:number)=>Number(sorted[Math.max(0,Math.ceil(sorted.length*p)-1)].toFixed(3));
  return {min:Number(sorted[0].toFixed(3)),p50:at(.5),p95:at(.95),max:Number(sorted[sorted.length-1].toFixed(3))};
}

function increment(counts:Record<string,number>,key:string) {counts[key]=(counts[key]??0)+1;}
function categoryKey(entity:ProductInput|CreatorInput) {
  return JSON.stringify([entity.categoryFact!.namespace,entity.categories[0]]);
}
function sameHistoricalCategory(product:ProductInput,creator:CreatorInput) {
  return product.categoryFact?.status==="historical"&&creator.categoryFact?.status==="historical"
    &&product.categoryFact.namespace===creator.categoryFact.namespace
    &&product.categories.some(label=>creator.categories.includes(label));
}
function categorySources(facts:CategoryFact[]) {
  const statuses:Record<string,number>={},namespaces:Record<string,number>={},timeBases:Record<string,number>={},transformVersions:Record<string,number>={};
  for(const fact of facts) {
    increment(statuses,fact.status);increment(namespaces,fact.namespace);
    increment(timeBases,fact.timeBasis);increment(transformVersions,fact.transformVersion??"not_recorded");
  }
  const observed=facts.map(fact=>fact.source.observedAt);
  return {statuses,namespaces,timeBases,transformVersions,observedAt:{min:Math.min(...observed),max:Math.max(...observed)}};
}
function object(value:unknown,scope:string):Record<string,unknown> {
  assert(value!==null&&typeof value==="object"&&!Array.isArray(value),`${scope}: expected an object.`);
  return value as Record<string,unknown>;
}
function assertNoExactLeakage(candidate:MatchCandidate) {
  assert(candidate.creator.market==="it"&&candidate.product.market==="it","First-source evaluation crossed the Italy market boundary.");
  assert.equal(candidate.features.exactUnits,null,"Historical profile recall leaked same-PID sales features.");
  assert(candidate.sources.includes("category")&&candidate.sources.every(source=>source==="category"),"Historical label recall used an unrelated source or cold-start fallback.");
  assert(candidate.features.categoryOverlap>0&&sameHistoricalCategory(candidate.product,candidate.creator),"Candidate labels or category namespaces do not match.");
  assert.equal(candidate.offers.length,0,"Historical profile recall acquired an offer.");
  assert.equal(candidate.creator.control,"unknown","Historical profile recall invented current relationship control.");
  assert.equal(candidate.creator.marketingStopped,null,"Historical profile recall invented a marketing preference.");
  assert.equal(candidate.readiness,"needs_facts","Historical labels were promoted to execution readiness.");
}

/** Tests historical label retrieval in a separate, newly imported store. It never supplies human relevance labels. */
export function evaluateItalyProfiles(store:MatchingStore,batch:MatchingBatch) {
  const products=batch.products??[],creators=batch.creators??[];
  assert(products.length>0&&creators.length>0,"Italy profile evaluation requires products and creators.");
  assert(products.every(product=>product.market==="it")&&creators.every(creator=>creator.market==="it"),"Italy profile evaluation requires an Italy-only batch.");
  assert.equal(new Set(products.map(product=>product.id)).size,products.length,"Duplicate product identities in profile input.");
  assert.equal(new Set(products.map(product=>product.pid)).size,products.length,"Duplicate product PIDs in profile input.");
  assert.equal(new Set(creators.map(creator=>creator.id)).size,creators.length,"Duplicate creator identities in profile input.");
  assert.equal(new Set(creators.map(creator=>creator.oecId)).size,creators.length,"Duplicate historical OEC identities in profile input.");
  assert(products.every(product=>product.categoryFact&&(product.categoryFact.status==="historical"?product.categories.length===1:product.categoryFact.status==="conflict"&&product.categories.length===0)),"Every evaluated product must carry a single historical top label or an explicit quarantined conflict.");
  assert(creators.every(creator=>creator.categoryFact?.status==="historical"&&creator.categories.length===1&&creator.oecId!==null&&!creator.externalIdentity),"Profile evaluation requires independent historical OEC profiles with a single category label.");
  assert(creators.every(creator=>creator.control==="unknown"&&creator.marketingStopped===null),"Historical profiles must preserve unknown current control and marketing preferences.");
  assert(creators.every(creator=>creator.priceMinMinor===null&&creator.priceMaxMinor===null&&creator.formats.length===0),"This evaluation expects the actual profile snapshot's missing price bands and content formats.");
  for(const key of ["evidence","offers","demands"] as const)assert.equal((batch[key]??[]).length,0,"Historical first-source input must not include sales, offers, or demand facts.");
  const initial=store.stats();
  assert.equal(initial.mode,"imported-offline","Profile evaluation requires a separate offline-import dataset.");
  assert.equal(initial.products,products.length,"Store product count differs from profile input.");
  assert.equal(initial.creators,creators.length,"Store creator count differs from profile input.");
  for(const key of ["evidence","offers","demands","runs","packets"] as const)assert.equal(initial[key],0,"Profile evaluation requires a newly imported store without unrelated facts or previous evaluations.");

  const categories=new Map<string,{namespace:string;label:string;count:number;representative:CreatorInput}>();
  for(const creator of [...creators].sort((a,b)=>a.id.localeCompare(b.id))) {
    const key=categoryKey(creator),group=categories.get(key);
    if(group)group.count++;
    else categories.set(key,{namespace:creator.categoryFact!.namespace,label:creator.categories[0],count:1,representative:creator});
  }
  const groups=[...categories.values()].sort((a,b)=>a.namespace.localeCompare(b.namespace)||a.label.localeCompare(b.label));
  const expectedByProduct=new Map(products.map(product=>[product.id,new Set(creators.filter(creator=>sameHistoricalCategory(product,creator)).map(creator=>creator.id))]));
  const queries:RecallQuery[]=[
    ...products.map(product=>({direction:"product" as const,subjectId:product.id,source:"first" as const,limit:50})),
    ...groups.map(group=>({direction:"creator" as const,subjectId:group.representative.id,source:"first" as const,limit:50})),
  ];
  const productRuns=new Map<string,MatchRun>(),uncachedMs:number[]=[],cachedMs:number[]=[],pairKeys=new Set<string>();
  const byProduct:{pid:string;categoryLabel:string|null;categoryStatus:CategoryFact["status"];historicalCategoryCandidates:number;returned:number;truncated:boolean}[]=[];
  const byCategory:{namespace:string;label:string;historicalProfiles:number;expectedProducts:number;returnedProducts:number}[]=[];
  const candidateSources:Record<string,number>={};
  let returnedOccurrences=0,creatorGroupsWithCandidates=0,maxRowsFetched=0;
  for(const query of queries) {
    const started=performance.now(),run=store.recall(query);
    uncachedMs.push(performance.now()-started);
    assert.equal(run.cacheHit,false,"Profile evaluation must begin without an existing recall cache.");
    assert.equal(run.market,"it","Profile evaluation crossed the Italy market boundary.");
    assert.equal(run.query.source,"first","Profile evaluation used a non-first-source query.");
    assert.equal(run.diagnostics.llmCalls,0,"Profile evaluation unexpectedly called a model.");
    assert.equal(run.diagnostics.billedTokens,0,"Profile evaluation unexpectedly incurred tokens.");
    assert.equal(run.diagnostics.fullCartesianEvaluated,false,"Profile recall unexpectedly evaluated the full Cartesian product.");
    maxRowsFetched=Math.max(maxRowsFetched,run.diagnostics.rowsFetched);
    const actualOtherIds=run.candidates.map(candidate=>query.direction==="product"?candidate.creator.id:candidate.product.id);
    assert.equal(new Set(actualOtherIds).size,actualOtherIds.length,"Profile recall returned duplicate candidate pairs.");
    for(const candidate of run.candidates) {
      assertNoExactLeakage(candidate);
      assert.equal(query.direction==="product"?candidate.product.id:candidate.creator.id,query.subjectId,"Profile recall returned a pair unrelated to its subject.");
      pairKeys.add(JSON.stringify([candidate.creator.id,candidate.product.id]));
      for(const source of candidate.sources)increment(candidateSources,source);
    }
    returnedOccurrences+=run.candidates.length;
    if(query.direction==="product") {
      const product=products.find(item=>item.id===query.subjectId)!;
      const expected=expectedByProduct.get(product.id)!;
      assert.equal(run.candidates.length,Math.min(50,expected.size),"Product recall returned an incorrect bounded historical-category count.");
      assert(actualOtherIds.every(id=>expected.has(id)),"Product recall returned a creator outside the matching historical category.");
      if(expected.size>50)assert.equal(run.diagnostics.truncated,true,"A bounded product result must disclose omitted historical-category candidates.");
      if(product.categoryFact!.status==="conflict")assert.equal(run.candidates.length,0,"Conflicting category was allowed into first-source exploration.");
      productRuns.set(product.id,run);
      byProduct.push({pid:product.pid,categoryLabel:product.categories[0]??null,categoryStatus:product.categoryFact!.status,historicalCategoryCandidates:expected.size,returned:run.candidates.length,truncated:run.diagnostics.truncated});
    } else {
      const group=groups.find(item=>item.representative.id===query.subjectId)!;
      const expected=new Set(products.filter(product=>sameHistoricalCategory(product,group.representative)).map(product=>product.id));
      assert(expected.size<=50,"Category representative evaluation exceeds its full-set comparison boundary.");
      assert.equal(actualOtherIds.length,expected.size,"Category representative did not return all and only matching historical products.");
      assert(actualOtherIds.every(id=>expected.has(id)),"Category representative received a mismatched or conflicting product.");
      if(run.candidates.length)creatorGroupsWithCandidates++;
      byCategory.push({namespace:group.namespace,label:group.label,historicalProfiles:group.count,expectedProducts:expected.size,returnedProducts:run.candidates.length});
    }

    const assessments=store.assessments(run.id);
    assert.equal(assessments.items.length,run.candidates.length,"Assessment scope differs from this run's actual candidate sample.");
    assert(assessments.items.every(item=>item.label===null&&item.note===""&&item.revision===0&&item.reviewedAt===null),"Profile evaluation found invented or pre-existing human assessments.");
    assert.equal(assessments.summary.total,run.candidates.length,"Assessment total includes candidates outside this run.");
    assert.equal(assessments.summary.reviewed,0,"Profile evaluation must not supply operator judgments.");
    assert.equal(assessments.summary.decided,0,"Profile evaluation must not supply suitability decisions.");
    assert.equal(assessments.summary.suitabilityRate,null,"Unassessed candidates must not have a suitability rate.");
    assert.equal(assessments.summary.coverage,0,"Unassessed candidates must have zero review coverage.");
    const cachedStarted=performance.now(),cached=store.recall(query);
    cachedMs.push(performance.now()-cachedStarted);
    assert.equal(cached.cacheHit,true,"An unchanged profile query missed its result cache.");
    assert.equal(cached.id,run.id,"Cached profile query did not reuse the same run.");
    assert.equal(cached.fingerprint,run.fingerprint,"Cached profile query changed its fingerprint.");
    assert.deepEqual(cached.candidates,run.candidates,"Cached profile query changed its candidate facts.");
  }

  const packetCharacters:number[]=[];
  for(const product of products.filter(item=>item.categoryFact!.status==="historical")) {
    const run=productRuns.get(product.id)!;
    assert(run.candidates.length>0,"A historical test product requires a candidate for bounded packet verification.");
    const candidate=run.candidates[0],packet=store.prepareReview(run.id,candidate.creator.id);
    assert.equal(packet.modelStatus,"not_called","Profile review packet unexpectedly called a model.");
    assert.equal(packet.estimatedTokens,null,"Profile packet must not infer billable tokens from characters.");
    assert.equal(packet.executable,false,"Profile review packet must not become executable.");
    assert.equal(packet.executionBlocked,true,"Profile review packet lost its execution block.");
    assert.equal(packet.payload.executionBlocked,true,"Profile review payload lost its execution block.");
    assert.equal(packet.payload.mode,"imported-offline","Profile review payload changed its data mode.");
    assert.equal(packet.candidates,1,"Single-product profile review must contain exactly one product.");
    assert.equal(packet.characters,JSON.stringify(packet.payload).length,"Profile packet character count differs from its actual payload.");
    assert(packet.characters>0&&packet.characters<=6000,"Profile review exceeds its bounded character budget.");
    const packetCreator=object(packet.payload.creator,"Review creator");
    assert.deepEqual(packetCreator.categoryFact,candidate.creator.categoryFact,"Review packet omitted or changed historical creator-category provenance.");
    assert.equal(packetCreator.control,"unknown","Review packet invented current relationship control.");
    assert.equal(packetCreator.marketingStopped,null,"Review packet invented a marketing preference.");
    assert(Array.isArray(packet.payload.candidates)&&packet.payload.candidates.length===1,"Review payload must contain exactly one candidate.");
    const item=object(packet.payload.candidates[0],"Review candidate"),packetProduct=object(item.product,"Review product");
    assert.deepEqual(packetProduct.categoryFact,candidate.product.categoryFact,"Review packet omitted or changed historical product-category provenance.");
    assert.equal(item.exactObservation,null,"Review packet leaked same-PID observations.");
    assert.equal(object(item.features,"Review features").exactUnits,null,"Review packet leaked same-PID sales features.");
    assert.deepEqual(item.offers,[],"Review packet acquired an offer.");
    assert.deepEqual(item.sources,["category"],"Review packet misrepresented its historical-category source.");
    packetCharacters.push(packet.characters);
  }
  const final=store.stats();
  for(const key of ["evidence","offers","demands","llmCalls","billedTokens"] as const)assert.equal(final[key],0,"Profile evaluation acquired unrelated evidence or model usage.");
  assert.equal(final.runs,queries.length,"Profile evaluation created an unexpected recall run.");
  assert.equal(final.packets,packetCharacters.length,"Profile evaluation created an unexpected review packet.");
  const structuralPairs=[...expectedByProduct.values()].reduce((total,ids)=>total+ids.size,0);
  return {
    evaluation:"historical_category_first_source_structure" as const,
    scope:"Checks bounded historical top-label retrieval and evidence packaging. Label equality is a discovery feature, not relevance ground truth, current profile verification, identity merging, or sending readiness.",
    qualityStatus:"awaiting_operator_assessment" as const,
    counts:{products:products.length,creators:creators.length,categoryGroups:groups.length,usableProductCategories:products.filter(product=>product.categoryFact!.status==="historical").length,conflictingProductCategories:products.filter(product=>product.categoryFact!.status==="conflict").length,fullCartesianPairs:products.length*creators.length,historicalCategoryCandidatePairs:structuralPairs,evidence:0,offers:0,demands:0},
    first:{productQueries:products.length,creatorRepresentativeQueries:groups.length,creatorGroupsWithCandidates,returnedCandidateOccurrences:returnedOccurrences,uniqueEvaluatedCandidatePairs:pairKeys.size,byProduct,byCategory,candidateSourceDistribution:candidateSources,noExactPidLeakage:true,unexpectedPairs:0,duplicatePairs:0},
    categorySources:{products:categorySources(products.map(product=>product.categoryFact!)),creators:categorySources(creators.map(creator=>creator.categoryFact!))},
    assessmentsAwaiting:{scope:"Only candidate pairs returned by this evaluation; not all profiles or the full structural candidate pool.",uniqueCandidatePairs:pairKeys.size,displayedCandidateOccurrences:returnedOccurrences,reviewed:0,suitable:0,unsuitable:0,insufficient:0,suitabilityRate:null,coverage:0,labelsWritten:0},
    timing:{unit:"milliseconds",scope:"Full synchronous recall calls only. Uncached means application result-cache miss, not cold disk or OS cache; assertions, assessments, and packet preparation are excluded.",uncached:{queries:uncachedMs.length,...distribution(uncachedMs)},cached:{queries:cachedMs.length,cacheHits:cachedMs.length,...distribution(cachedMs)},maxRowsFetched},
    reviewPackets:{count:packetCharacters.length,characters:distribution(packetCharacters),candidatesPerPacket:1,categoryProvenancePreserved:true,unknownRelationshipControlsPreserved:true,estimatedTokens:null,modelCalls:0,executable:false,executionBlocked:true},
    modelCalls:0,billedTokens:0,estimatedTokens:null,realSends:0,
  };
}
