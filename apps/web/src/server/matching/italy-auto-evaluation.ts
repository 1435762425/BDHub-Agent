import assert from "node:assert/strict";
import {performance} from "node:perf_hooks";
import {isDeepStrictEqual} from "node:util";
import type {CreatorInput, MatchCandidate, MatchingBatch, MatchRun, ProductInput, RecallQuery, ReviewPacket} from "../../features/matching/contracts.ts";
import type {MatchingStore} from "./store.ts";

function distribution(values:number[]) {
  if(!values.length)return {min:null,p50:null,p95:null,max:null};
  const sorted=[...values].sort((a,b)=>a-b);
  const at=(p:number)=>Number(sorted[Math.max(0,Math.ceil(sorted.length*p)-1)].toFixed(3));
  return {min:Number(sorted[0].toFixed(3)),p50:at(.5),p95:at(.95),max:Number(sorted.at(-1)!.toFixed(3))};
}
function increment(counts:Record<string,number>,key:string) {counts[key]=(counts[key]??0)+1;}
// Do not embed private creator records in an assertion error or an evaluation report.
function same(actual:unknown,expected:unknown,message:string) {assert(isDeepStrictEqual(actual,expected),message);}
function object(value:unknown):Record<string,unknown> {
  assert(value!==null&&typeof value==="object"&&!Array.isArray(value),"Review packet contains an invalid object.");
  return value as Record<string,unknown>;
}
function categoryOverlap(product:ProductInput,creator:CreatorInput) {
  if(!product.categoryFact||!creator.categoryFact||product.categoryFact.status==="conflict"||creator.categoryFact.status==="conflict"||product.categoryFact.namespace!==creator.categoryFact.namespace)return 0;
  return product.categories.filter(category=>creator.categories.includes(category)).length;
}
const lexical=(a:string,b:string)=>a<b?-1:a>b?1:0;

/** Independent oracle for this one-source cohort, not an import of the production ranking function. */
function expectedCreators(product:ProductInput,creators:CreatorInput[]) {
  return creators.filter(creator=>categoryOverlap(product,creator)>0).sort((a,b)=>
    categoryOverlap(product,b)-categoryOverlap(product,a)
    ||b.profileSignals!.unitsSold!-a.profileSignals!.unitsSold!
    ||b.profileSignals!.avgViews!-a.profileSignals!.avgViews!
    ||lexical(a.id,b.id));
}

function assertAnalysis(candidate:MatchCandidate,policyVersion:string) {
  assert(candidate.creator.market==="it"&&candidate.product.market==="it","Automatic analysis crossed the Italy market boundary.");
  assert.equal(candidate.features.exactUnits,null,"First-source analysis leaked exact-product sales.");
  same(candidate.sources,["category"],"Profile-only analysis used an unrelated retrieval source.");
  assert.equal(candidate.features.categoryOverlap,categoryOverlap(candidate.product,candidate.creator),"Category-overlap evidence differs from the independent oracle.");
  assert(candidate.features.categoryOverlap>0,"Automatic analysis accepted an unrelated or conflicting category.");
  assert.equal(candidate.offers.length,0,"Profile-only analysis acquired an offer.");
  assert.equal(candidate.readiness,"needs_facts","Offline analysis changed execution readiness.");
  assert.equal(candidate.creator.control,"unknown","Offline analysis invented current relationship control.");
  assert.equal(candidate.creator.marketingStopped,null,"Offline analysis invented a marketing preference.");
  assert.equal(candidate.analysis.policyVersion,policyVersion,"Candidate explanation uses a different policy from its run.");
  assert.equal(candidate.analysis.tier,"category_aligned","Category evidence has an incorrect explanation tier.");
  assert(candidate.analysis.summary.length>0&&candidate.analysis.positiveEvidence.length>0,"Automatic analysis did not produce an evidence-based conclusion.");
  assert(candidate.analysis.limitations.length>0,"Automatic analysis omitted the limits of its evidence.");
  assert.equal(candidate.analysis.profileSummary.signalStatus,"observed","Actual measurements were not represented in the analysis.");
  same(candidate.analysis.profileSummary.signals,candidate.creator.profileSignals,"Analysis changed its source measurements.");
  assert.equal(candidate.analysis.profileSummary.comparison,"same_scope_only","Analysis claimed comparison beyond this source cohort.");
}

function assertHumanLabelsUnchanged(store:MatchingStore,run:MatchRun) {
  const assessments=store.assessments(run.id);
  assert.equal(assessments.items.length,run.candidates.length,"Human-assessment scope differs from the actual candidate sample.");
  assert(assessments.items.every(item=>item.label===null&&item.note===""&&item.revision===0&&item.reviewedAt===null),"Automatic analysis created or reused an operator judgment.");
  assert.equal(assessments.summary.reviewed,0,"Automatic analysis must not supply operator labels.");
  assert.equal(assessments.summary.suitabilityRate,null,"Automatic research cannot fabricate a human suitability rate.");
}

function assertPacket(packet:ReviewPacket,run:MatchRun) {
  assert.equal(packet.modelStatus,"not_called","Packet preparation unexpectedly called a model.");
  assert.equal(packet.estimatedTokens,null,"Character counts were misrepresented as measured model tokens.");
  assert.equal(packet.executable,false,"Offline analysis created an executable packet.");
  assert.equal(packet.executionBlocked,true,"Offline analysis lost its execution block.");
  assert.equal(packet.payload.executionBlocked,true,"Packet payload lost its execution block.");
  assert.equal(packet.payload.mode,"imported-offline","Packet payload changed dataset mode.");
  assert.equal(packet.characters,JSON.stringify(packet.payload).length,"Packet character count differs from its serialized payload.");
  assert(packet.characters>0&&packet.characters<=6000,"Necessary profile context exceeds the actual 6000-character budget.");
  assert(packet.candidates>0&&packet.candidates<=4,"Italy packet contains an unexpected number of products.");
  assert(Array.isArray(packet.payload.candidates),"Packet has no candidate array.");
  assert.equal(packet.payload.candidates.length,packet.candidates,"Packet count differs from its actual candidate array.");
  const expected=run.candidates.filter(candidate=>candidate.creator.id===packet.creatorId);
  const creator=object(packet.payload.creator);
  same(creator.categories,expected[0].creator.categories,"Packet lost the creator's multi-category profile.");
  assert.equal(creator.control,"unknown","Packet invented relationship control.");
  assert.equal(creator.marketingStopped,null,"Packet invented a marketing preference.");
  assert.equal(packet.payload.omittedCandidates,expected.length-packet.candidates,"Packet failed to disclose products omitted for context size.");
  const seen=new Set<string>();
  for(const [index,value] of packet.payload.candidates.entries()) {
    const item=object(value),product=object(item.product);
    assert(product.id===expected[index].product.id,"Packet changed product ordering or included an unrelated product.");
    assert(!seen.has(product.id as string),"Packet contains duplicate products.");seen.add(product.id as string);
    assert.equal(item.exactObservation,null,"Packet leaked exact-product observations.");
    assert.equal(object(item.features).exactUnits,null,"Packet leaked exact-product sales features.");
    same(item.offers,[],"Packet acquired an offer.");
    same(item.sources,["category"],"Packet changed its profile evidence source.");
  }
}

/** Complete automatic offline analysis. This evaluates evidence/ranking contracts, not conversion prediction. */
export function evaluateItalyAutoAnalysis(store:MatchingStore,batch:MatchingBatch) {
  const products=batch.products??[],creators=batch.creators??[];
  assert.equal(products.length,5,"This evaluation requires the five-product Italy source cohort.");
  assert.equal(creators.length,3978,"This evaluation requires all 3978 historical Italy profiles.");
  assert(products.every(product=>product.market==="it")&&creators.every(creator=>creator.market==="it"),"Italy automatic evaluation requires an Italy-only batch.");
  assert.equal(new Set(products.map(product=>product.id)).size,products.length,"Duplicate product identities in evaluation input.");
  assert.equal(new Set(creators.map(creator=>creator.id)).size,creators.length,"Duplicate creator identities in evaluation input.");
  assert.equal(new Set(creators.map(creator=>creator.oecId)).size,creators.length,"Duplicate OEC identities in evaluation input.");
  assert(products.every(product=>product.categoryFact&&(product.categoryFact.status==="historical"?product.categories.length===1:product.categoryFact.status==="conflict"&&product.categories.length===0)),"Each product must have one usable category or an explicitly quarantined conflict.");
  assert(creators.every(creator=>creator.categoryFact?.status==="historical"&&creator.categories.length>0&&creator.oecId!==null&&!creator.externalIdentity),"Evaluation requires independently identified multi-category OEC profiles.");
  assert(creators.every(creator=>creator.profileSignals&&creator.profileSignals.unitsSold!==null&&creator.profileSignals.avgViews!==null),"This cohort oracle requires recorded units and average views for every profile.");
  assert.equal(new Set(creators.map(creator=>creator.profileSignals!.comparisonScope)).size,1,"This oracle must not compare different source cohorts by absolute measurements.");
  for(const key of ["evidence","offers","demands"] as const)assert.equal((batch[key]??[]).length,0,"Profile analysis input contains unrelated business facts.");
  const initial=store.stats();
  assert.equal(initial.mode,"imported-offline","Automatic evaluation requires an independent offline dataset.");
  assert.equal(initial.products,products.length);assert.equal(initial.creators,creators.length);
  for(const key of ["evidence","offers","demands","runs","packets"] as const)assert.equal(initial[key],0,"Evaluation requires a newly imported store without previous runs or business facts.");

  const expectedByProduct=new Map(products.map(product=>[product.id,expectedCreators(product,creators)]));
  const productRuns=new Map<string,MatchRun>();
  const byProduct:{pid:string;category:string|null;categoryStatus:string;profileCandidates:number;returned:number;truncated:boolean;oracleOrderVerified:boolean}[]=[];
  const uncachedMs:number[]=[],cachedMs:number[]=[],packetCharacters:number[]=[],packetProductCounts:number[]=[];
  const tiers:Record<string,number>={},sources:Record<string,number>={},creatorMatchCounts:Record<string,number>={};
  const uniquePairs=new Set<string>();
  let analyzedCreatorsWithMatches=0,returnedOccurrences=0,maxRowsFetched=0,creatorPackets=0,omittedProducts=0,packetsWithOmissions=0;
  let policyVersion:string|null=null;

  function evaluateQuery(query:RecallQuery) {
    const started=performance.now(),run=store.recall(query);uncachedMs.push(performance.now()-started);
    assert.equal(run.cacheHit,false,"A newly imported evaluation reused a prior result.");
    assert.equal(run.market,"it");assert.equal(run.query.source,"first");
    assert.equal(run.diagnostics.llmCalls,0);assert.equal(run.diagnostics.billedTokens,0);
    assert.equal(run.diagnostics.fullCartesianEvaluated,false,"Runtime evaluated a Cartesian product.");
    assert.equal(run.analysisPolicy.mode,"profile-first");
    assert.equal(run.analysisPolicy.profileAge,"ignore_for_analysis");
    assert.equal(run.analysisPolicy.priceBand,"context_only");
    assert.equal(run.analysisPolicy.contentFormat,"context_only");
    assert.equal(run.analysisPolicy.modelCalls,0);
    policyVersion??=run.analysisPolicy.version;
    assert.equal(run.analysisPolicy.version,policyVersion,"Automatic policy changed within a fixed evaluation batch.");
    maxRowsFetched=Math.max(maxRowsFetched,run.diagnostics.rowsFetched);
    const others=run.candidates.map(candidate=>query.direction==="product"?candidate.creator.id:candidate.product.id);
    assert.equal(new Set(others).size,others.length,"Recall returned duplicate candidate pairs.");
    for(const candidate of run.candidates) {
      assertAnalysis(candidate,policyVersion);
      assert((query.direction==="product"?candidate.product.id:candidate.creator.id)===query.subjectId,"Recall returned an unrelated subject pair.");
      const key=JSON.stringify([candidate.creator.id,candidate.product.id]);
      if(!uniquePairs.has(key)) {increment(tiers,candidate.analysis.tier);for(const source of candidate.sources)increment(sources,source);}
      uniquePairs.add(key);
    }
    returnedOccurrences+=run.candidates.length;
    assertHumanLabelsUnchanged(store,run);
    const cachedStarted=performance.now(),cached=store.recall(query);cachedMs.push(performance.now()-cachedStarted);
    assert.equal(cached.cacheHit,true,"Unchanged query failed to reuse its result cache.");
    assert.equal(cached.id,run.id);assert.equal(cached.fingerprint,run.fingerprint);
    same(cached.candidates,run.candidates,"Cached facts differ from the completed automatic analysis.");
    return run;
  }

  for(const product of products) {
    const run=evaluateQuery({direction:"product",subjectId:product.id,source:"first",limit:50});
    const expected=expectedByProduct.get(product.id)!;
    same(run.candidates.map(candidate=>candidate.creator.id),expected.slice(0,50).map(creator=>creator.id),"Product top 50 differs from the independent category/units/views/identity oracle.");
    if(expected.length>50)assert.equal(run.diagnostics.truncated,true,"Bounded recall did not disclose omitted candidates.");
    if(product.categoryFact!.status==="conflict")assert.equal(run.candidates.length,0,"Conflicting product escaped category quarantine.");
    productRuns.set(product.id,run);
    byProduct.push({pid:product.pid,category:product.categories[0]??null,categoryStatus:product.categoryFact!.status,profileCandidates:expected.length,returned:run.candidates.length,truncated:run.diagnostics.truncated,oracleOrderVerified:true});
  }

  for(const creator of creators) {
    const run=evaluateQuery({direction:"creator",subjectId:creator.id,source:"first",limit:50});
    const expected=products.filter(product=>categoryOverlap(product,creator)>0).map(product=>product.id).sort(lexical);
    same(run.candidates.map(candidate=>candidate.product.id).sort(lexical),expected,"Creator recall did not return all and only its compatible product categories.");
    increment(creatorMatchCounts,String(expected.length));
    if(!expected.length)continue;
    analyzedCreatorsWithMatches++;
    // A preparation failure aborts the evaluation; no oversized or failed packet is silently skipped.
    const packet=store.prepareReview(run.id,creator.id);assertPacket(packet,run);
    packetCharacters.push(packet.characters);packetProductCounts.push(packet.candidates);creatorPackets++;
    const omitted=expected.length-packet.candidates;omittedProducts+=omitted;if(omitted)packetsWithOmissions++;
  }

  const representativePackets:{pid:string;characters:number;products:number}[]=[];
  for(const product of products.filter(product=>product.categoryFact!.status!=="conflict")) {
    const run=productRuns.get(product.id)!;
    assert(run.candidates.length>0,"Usable Italy product has no representative profile candidate.");
    const packet=store.prepareReview(run.id,run.candidates[0].creator.id);assertPacket(packet,run);
    assert.equal(packet.candidates,1,"Single-product representative packet has an unexpected candidate count.");
    packetCharacters.push(packet.characters);packetProductCounts.push(packet.candidates);
    representativePackets.push({pid:product.pid,characters:packet.characters,products:packet.candidates});
  }
  const expectedPairCount=[...expectedByProduct.values()].reduce((total,rows)=>total+rows.length,0);
  assert.equal(uniquePairs.size,expectedPairCount,"All-creator analysis failed to cover the full compatible pair set.");
  const final=store.stats();
  for(const key of ["evidence","offers","demands","llmCalls","billedTokens"] as const)assert.equal(final[key],0,"Automatic evaluation acquired unrelated facts or model usage.");
  assert.equal(final.runs,products.length+creators.length,"Evaluation created unexpected extra runs.");
  assert.equal(final.packets,packetCharacters.length,"Evaluation created unexpected extra review packets.");
  const metricsCoverage=Object.fromEntries((["followers","unitsSold","avgViews","gmvValue"] as const).map(key=>[key,{known:creators.filter(creator=>creator.profileSignals![key]!==null).length,missing:creators.filter(creator=>creator.profileSignals![key]===null).length}]));
  return {
    evaluation:"existing_profile_automatic_analysis" as const,status:"completed" as const,qualityStatus:"automatic_evidence_analysis_complete" as const,
    scope:"All 3978 existing Italy profiles analyzed automatically. Independent category and ordering oracles verify retrieval and explanations; this is not a claim about willingness, conversion accuracy, or current execution authority.",
    policy:{version:policyVersion,matchingVersion:final.matchingVersion,profileAge:"ignored_for_analysis",priceBand:"context_only",contentFormat:"context_only",comparison:"same_source_scope_units_then_avg_views",gmvRanking:false,assessmentRequired:false},
    counts:{products:products.length,creators:creators.length,autoAnalyzedCreators:creators.length,creatorsWithMatches:analyzedCreatorsWithMatches,creatorsWithoutMatchingProducts:creators.length-analyzedCreatorsWithMatches,candidatePairs:uniquePairs.size,fullCartesianPairsForOracleOnly:products.length*creators.length,categoryGroups:new Set(creators.flatMap(creator=>creator.categories)).size,conflictingProducts:products.filter(product=>product.categoryFact!.status==="conflict").length},
    first:{productQueries:products.length,creatorQueries:creators.length,returnedCandidateOccurrences:returnedOccurrences,byProduct,creatorMatchCountDistribution:creatorMatchCounts,analysisTierDistribution:tiers,candidateSourceDistribution:sources,independentTop50OrderVerified:true,allCreatorProductSetsVerified:true,noExactPidLeakage:true,unexpectedPairs:0,duplicatePairs:0},
    metricsCoverage,
    timing:{unit:"milliseconds",scope:"Synchronous recall only; result-cache miss is not cold disk. Assertions, label reads and packet preparation excluded.",uncached:{queries:uncachedMs.length,...distribution(uncachedMs)},cached:{queries:cachedMs.length,cacheHits:cachedMs.length,...distribution(cachedMs)},maxRowsFetched},
    reviewPackets:{count:packetCharacters.length,creatorPackets,representativePackets,characters:distribution(packetCharacters),productsPerPacket:distribution(packetProductCounts),packetsWithOmissions,omittedProducts,preparationFailures:0,allWithinCharacterBudget:true,estimatedTokens:null,modelCalls:0,executable:false,executionBlocked:true},
    humanAssessments:{requiredForAutomaticAnalysis:false,labelsWritten:0,assessmentsWritten:0,reviewed:0,suitabilityRate:null},
    assessmentsWritten:0,modelCalls:0,billedTokens:0,estimatedTokens:null,realSends:0,
  };
}
