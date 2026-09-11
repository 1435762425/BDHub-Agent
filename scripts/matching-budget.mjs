import {pathToFileURL} from "node:url";

// Planning assumptions, not live prices, measured model usage or platform capacity.
export const assumptions = {
  products:5000, creators:20000, dailyDecisions:2000,
  productSemanticChangeRate:0.02, creatorSemanticChangeRate:0.01,
  extractionRate:0.10, extractionInput:600, extractionOutput:100,
  decisionInput:1200, decisionOutput:200, overheadRate:0.10,
  productEmbeddingTokens:120, creatorEmbeddingTokens:180,
  inputPricePerMillion:1, outputPricePerMillion:5, embeddingPricePerMillion:0.05,
  dailyBudget:10,
};

export function estimateMatchingBudget(overrides={}) {
  const p={...assumptions,...overrides};
  for(const [key,value] of Object.entries(p)) if(typeof value!=="number"||!Number.isFinite(value)||value<0) throw new Error(`Invalid nonnegative numeric parameter: ${key}`);
  for(const key of ["productSemanticChangeRate","creatorSemanticChangeRate","extractionRate","overheadRate"]) if(p[key]>1)throw new Error(`Rate outside [0,1]: ${key}`);
  for(const key of ["products","creators","dailyDecisions"])if(!Number.isSafeInteger(p[key]))throw new Error(`Expected whole count: ${key}`);
  const changedProducts=Math.ceil(p.products*p.productSemanticChangeRate);
  const changedCreators=Math.ceil(p.creators*p.creatorSemanticChangeRate);
  const extractions=Math.ceil((changedProducts+changedCreators)*p.extractionRate);
  const decisions=Math.ceil(p.dailyDecisions*(1+p.overheadRate));
  const extractionInput=extractions*p.extractionInput,extractionOutput=extractions*p.extractionOutput;
  const decisionInput=decisions*p.decisionInput,decisionOutput=decisions*p.decisionOutput;
  const embeddingTokens=changedProducts*p.productEmbeddingTokens+changedCreators*p.creatorEmbeddingTokens;
  const fixedCost=(extractionInput*p.inputPricePerMillion+extractionOutput*p.outputPricePerMillion+embeddingTokens*p.embeddingPricePerMillion)/1e6;
  const decisionUnit=(p.decisionInput*p.inputPricePerMillion+p.decisionOutput*p.outputPricePerMillion)/1e6;
  const dailyCost=fixedCost+decisions*decisionUnit;
  const initialExtractions=Math.ceil((p.products+p.creators)*p.extractionRate);
  const initialInput=initialExtractions*p.extractionInput,initialOutput=initialExtractions*p.extractionOutput;
  const initialEmbeddings=p.products*p.productEmbeddingTokens+p.creators*p.creatorEmbeddingTokens;
  const initialCost=(initialInput*p.inputPricePerMillion+initialOutput*p.outputPricePerMillion+initialEmbeddings*p.embeddingPricePerMillion)/1e6;
  const maxPaidCalls=decisionUnit>0?Math.floor(Math.max(0,p.dailyBudget-fixedCost)/decisionUnit):null;
  const affordableDecisions=maxPaidCalls===null?null:Math.floor(maxPaidCalls/(1+p.overheadRate));
  return {parameters:p,cartesianPairs:p.products*p.creators,changedProducts,changedCreators,extractions,decisions,
    daily:{inputTokens:extractionInput+decisionInput,outputTokens:extractionOutput+decisionOutput,llmTokens:extractionInput+decisionInput+extractionOutput+decisionOutput,embeddingTokens,costUsd:dailyCost,fixedCostUsd:fixedCost,affordableDecisions},
    initial:{extractions:initialExtractions,llmTokens:initialInput+initialOutput,embeddingTokens:initialEmbeddings,costUsd:initialCost},
    vectorRawMiB:(p.products+p.creators)*384*4/1024/1024,
  };
}

export const deepseekFlashPricing = Object.freeze({
  checkedOn:"2026-09-11", model:"DeepSeek-V4.1-Flash", apiModel:"deepseek-flash", currency:"CNY",
  source:"https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
  offPeak:{inputMiss:1,inputHit:0.02,output:4},
  peak:{inputMiss:2,inputHit:0.04,output:8},
  peakHours:"北京时间周一至周五09:00–12:00、14:00–18:00；其余为空闲时段",
});

export function estimateDeepSeekFlashBudget(overrides={}, {inputCacheHitRatio=0,extraReasoningTokensPerDecision=0}={}) {
  if(!Number.isFinite(inputCacheHitRatio)||inputCacheHitRatio<0||inputCacheHitRatio>1)throw new Error("Cache ratio must be in [0,1]");
  if(!Number.isSafeInteger(extraReasoningTokensPerDecision)||extraReasoningTokensPerDecision<0)throw new Error("Extra reasoning tokens must be a nonnegative integer");
  const base=estimateMatchingBudget(overrides),p=base.parameters;
  const input=base.daily.inputTokens;
  const hit=Math.round(input*inputCacheHitRatio),miss=input-hit;
  const output=base.daily.outputTokens+base.decisions*extraReasoningTokensPerDecision;
  const price=tier=>(miss*tier.inputMiss+hit*tier.inputHit+output*tier.output)/1e6;
  const initialInput=base.initial.extractions*p.extractionInput;
  const initialOutput=base.initial.extractions*p.extractionOutput;
  return {pricing:deepseekFlashPricing,products:p.products,creators:p.creators,dailyDecisions:p.dailyDecisions,
    paidDecisionCalls:base.decisions,featureExtractionCalls:base.extractions,
    inputTokens:input,cacheHitTokens:hit,cacheMissTokens:miss,outputTokens:output,
    extraReasoningTokensPerDecision,
    dailyCny:{offPeak:price(deepseekFlashPricing.offPeak),peak:price(deepseekFlashPricing.peak)},
    monthly30DaysCny:{offPeak:price(deepseekFlashPricing.offPeak)*30,weekdaysPeak:price(deepseekFlashPricing.peak)*22+price(deepseekFlashPricing.offPeak)*8},
    monthAssumption:"30天=22个工作日+8个周末；上端仅工作日按高峰，周末按空闲",
    initialFeatureExtractionCny:{offPeak:(initialInput+initialOutput*4)/1e6,peak:(initialInput*2+initialOutput*8)/1e6},
    excluded:{dailyEmbeddingTokens:base.daily.embeddingTokens,initialEmbeddingTokens:base.initial.embeddingTokens},
    scope:"仅匹配/首轮话术与增量特征提取；不含向量、后续多轮服务、图片/视频模型、采集或主机。无真实API调用。",
  };
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href) {
  const scenarios=[
    {label:"较小",products:1000,creators:10000,dailyDecisions:500},
    {label:"常用测算",products:5000,creators:20000,dailyDecisions:2000},
    {label:"压力假设",products:10000,creators:50000,dailyDecisions:5000},
  ];
  const args=process.argv.slice(2);
  if(args.length && !(args.length===2&&args[0]==="--model"&&args[1]==="deepseek-flash"))throw new Error("Usage: node scripts/matching-budget.mjs [--model deepseek-flash]");
  console.log(JSON.stringify(args.length
    ? {notice:"DeepSeek官方人民币报价；用量为原规划假设，按无缓存、200计费输出测算，不代表默认思考模式的实际用量。",scenarios:scenarios.map(({label,...p})=>({label,...estimateDeepSeekFlashBudget(p)}))}
    : {notice:"所有单价、变化率和用量均为规划占位参数，不是当前模型报价或平台发送能力。只计匹配和首轮话术，不含后续多轮对话/采集/主机费用。",scenarios:scenarios.map(({label,...p})=>({label,...estimateMatchingBudget(p)}))},null,2));
}
