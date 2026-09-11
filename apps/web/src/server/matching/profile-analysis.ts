import type {AnalysisPolicy,CandidateAnalysis,MatchCandidate,MatchCreator,MatchingDataset,MatchProduct,CandidateSource} from "../../features/matching/contracts.ts";

export const PROFILE_POLICY_VERSION="profile-first-v1";
export function analysisPolicy(mode:MatchingDataset["mode"]):AnalysisPolicy {
  const imported=mode==="imported-offline";
  return {version:imported?PROFILE_POLICY_VERSION:"synthetic-evidence-v1",mode:imported?"profile-first":"synthetic-demo",profileAge:"ignore_for_analysis",priceBand:imported?"context_only":"ranking_signal",contentFormat:imported?"context_only":"ranking_signal",ranking:imported?["明确需求","精确同品证据","共同类目数量","同口径组内销量、平均观看；不同组交错展示"]:["明确需求","精确同品证据","价格带","形式","共同类目数量"],modelCalls:0};
}

/** Missing measurements occupy an independent lane, never a fabricated zero or a negative relevance label. */
export function profileRankScope(creator:MatchCreator):string {
  const signals=creator.profileSignals;
  return JSON.stringify([signals?.comparisonScope??"profile_only",signals?.unitsSold!==null&&signals?.unitsSold!==undefined?"units_known":"units_unknown",signals?.avgViews!==null&&signals?.avgViews!==undefined?"views_known":"views_unknown"]);
}
const lexical=(a:string,b:string)=>a<b?-1:a>b?1:0;
function withinScope(a:MatchCandidate,b:MatchCandidate) {
  const aa=a.creator.profileSignals,bb=b.creator.profileSignals;
  // Callers group by measurement availability and source scope before this comparison.
  if(aa?.unitsSold!==null&&aa?.unitsSold!==undefined&&bb?.unitsSold!==null&&bb?.unitsSold!==undefined&&aa.unitsSold!==bb.unitsSold)return bb.unitsSold-aa.unitsSold;
  if(aa?.avgViews!==null&&aa?.avgViews!==undefined&&bb?.avgViews!==null&&bb?.avgViews!==undefined&&aa.avgViews!==bb.avgViews)return bb.avgViews-aa.avgViews;
  return lexical(a.creator.id,b.creator.id)||lexical(a.product.id,b.product.id);
}
export function orderProfileCandidates(candidates:MatchCandidate[]):MatchCandidate[] {
  const groups=new Map<string,{priority:number[];lanes:Map<string,MatchCandidate[]>}>();
  for(const candidate of candidates) {
    const priority=[candidate.sources.includes("explicit_demand")?1:0,candidate.sources.includes("exact_pid")?1:0,candidate.features.categoryOverlap];
    const key=JSON.stringify(priority),group=groups.get(key)??{priority,lanes:new Map<string,MatchCandidate[]>()};groups.set(key,group);
    const lane=profileRankScope(candidate.creator),items=group.lanes.get(lane)??[];items.push(candidate);group.lanes.set(lane,items);
  }
  const result:MatchCandidate[]=[];
  for(const group of [...groups.values()].sort((a,b)=>{for(let i=0;i<a.priority.length;i++)if(a.priority[i]!==b.priority[i])return b.priority[i]-a.priority[i];return 0;})) {
    const lanes=[...group.lanes.entries()].sort(([a],[b])=>lexical(a,b)).map(([,items])=>items.sort(withinScope));
    for(let index=0;index<Math.max(...lanes.map(items=>items.length));index++)for(const lane of lanes)if(lane[index])result.push(lane[index]);
  }
  return result;
}

export function analyzeProfile(product:MatchProduct,creator:MatchCreator,sources:Set<CandidateSource>,categoryOverlap:number,exactUnits:number|null,policy:AnalysisPolicy):CandidateAnalysis {
  const positiveEvidence:string[]=[],limitations:string[]=[],signals=creator.profileSignals??null;
  const tier=sources.has("explicit_demand")?"explicit_demand":sources.has("exact_pid")?"same_product":categoryOverlap>0?"category_aligned":"exploration";
  if(sources.has("explicit_demand"))positiveEvidence.push("已有明确需求记录，可据其商品或类目诉求继续研究。");
  if(sources.has("exact_pid"))positiveEvidence.push(`同市场精确 PID 有正销量观测${exactUnits===null?"":` ${exactUnits} 件`}；仅指该商品观察，不与画像总销量混算。`);
  if(categoryOverlap>0)positiveEvidence.push(`商品与达人已有画像共有 ${categoryOverlap} 个类目：${product.categories.filter(category=>creator.categories.includes(category)).join("、")}。`);
  if(signals?.unitsSold!==null&&signals?.unitsSold!==undefined)positiveEvidence.push(`画像来源记录销量 ${signals.unitsSold} 件${signals.periodLabel?`（${signals.periodLabel}）`:"（统计周期未提供）"}。`);
  if(signals?.avgViews!==null&&signals?.avgViews!==undefined)positiveEvidence.push(`来源平均观看 ${signals.avgViews}；不推算合作成功率。`);
  if(!signals)limitations.push("已有类目可供分析；表现指标未提供，保留为画像候选，不按零销量或低价值处理。");
  else {
    if(signals.unitsSold===null)limitations.push("销量未知，与已观测零销量分开，保留独立展示位置。");
    if(signals.avgViews===null)limitations.push("平均观看未知，不推断内容表现较差。");
    if(!signals.periodLabel||signals.source.windowStart===null||signals.source.windowEnd===null)limitations.push("来源未提供完整统计窗口；只在相同来源口径组内辅助排序，不称近期增长或30天业绩。");
  }
  if(!categoryOverlap)limitations.push("缺少共同类目证据；明确需求或同品记录可独立支持研究，否则保留探索结论。");
  if(product.categoryFact?.status==="conflict"||creator.categoryFact?.status==="conflict")limitations.push("分类存在来源冲突，不使用该分类作适配依据。");
  limitations.push("分析基于已有事实，不代表合作意愿、成交预测或执行授权。");
  const summaries={explicit_demand:"明确需求支持继续研究",same_product:"已有同品证据支持继续研究",category_aligned:"已有画像与商品类目相符",exploration:"保留为探索候选"};
  return {policyVersion:policy.version,tier,summary:summaries[tier],positiveEvidence,limitations,profileSummary:{categories:creator.categories,signals,signalStatus:signals&&(signals.unitsSold!==null||signals.avgViews!==null)?"observed":"profile_only",comparison:"same_scope_only"}};
}
