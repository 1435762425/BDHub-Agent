import {createHash} from "node:crypto";
import type {MatchingBatch,ProductEvidence} from "../../features/matching/contracts.ts";
import type {SecondPilotCaseInput,SecondPilotProduct} from "../../features/second-pilot/contracts.ts";
import {draftSecondIntro,ITALIAN_PRODUCT_NAMES} from "./skills.ts";

function stable(v:unknown):string {if(Array.isArray(v))return `[${v.map(stable).join(",")}]`;if(v&&typeof v==="object")return `{${Object.entries(v).sort(([a],[b])=>a<b?-1:a>b?1:0).map(([k,x])=>`${JSON.stringify(k)}:${stable(x)}`).join(",")}}`;return JSON.stringify(v);}
function hash(v:unknown){return createHash("sha256").update(stable(v)).digest("hex");}

/** One research relationship per Kalodata identity. No handle-to-OEC promotion and no synthetic Offer. */
export function buildItalySecondCases(batch:MatchingBatch,datasetId:string,readinessFingerprint:string|null=null):SecondPilotCaseInput[] {
  if(!datasetId.startsWith("italy-pilot-"))throw new Error("Second pilot requires the explicit Italy exact-PID dataset.");
  const products=new Map((batch.products??[]).map(p=>[p.id,p])),byPid=new Map((batch.products??[]).map(p=>[p.pid,p]));
  const creators=new Map((batch.creators??[]).map(c=>[c.id,c]));
  if(products.size!==(batch.products??[]).length||byPid.size!==products.size||creators.size!==(batch.creators??[]).length)throw new Error("Source identities must be unique.");
  if([...products.values()].some(p=>p.market!=="it")||[...creators.values()].some(c=>c.market!=="it"))throw new Error("Second pilot source must be Italy-only.");
  const grouped=new Map<string,Map<string,ProductEvidence[]>>();
  for(const edge of batch.evidence??[]) {
    if(edge.market!=="it"||!creators.has(edge.creatorId)||!byPid.has(edge.pid)||!Number.isSafeInteger(edge.units)||edge.units<=0)throw new Error("Every second-pilot observation must be an exact same-market positive product edge.");
    if(edge.source.windowStart===null||edge.source.windowEnd===null||edge.source.windowStart>edge.source.windowEnd)throw new Error("Second-pilot evidence must preserve its source window.");
    const pairs=grouped.get(edge.creatorId)??new Map<string,ProductEvidence[]>(),observations=pairs.get(edge.pid)??[];
    observations.push(edge);pairs.set(edge.pid,observations);grouped.set(edge.creatorId,pairs);
  }
  const cases:SecondPilotCaseInput[]=[],seenExternal=new Set<string>();
  for(const [creatorId,pairs] of [...grouped].sort(([a],[b])=>a<b?-1:a>b?1:0)) {
    const creator=creators.get(creatorId)!;
    if(creator.externalIdentity?.namespace!=="kalodata"||typeof creator.externalIdentity.id!=="string"||!/^\d+$/.test(creator.externalIdentity.id)||seenExternal.has(creator.externalIdentity.id))throw new Error("Each case requires one unique research identity; never infer OEC from it.");
    seenExternal.add(creator.externalIdentity.id);
    const handle=creator.name.replace(/^@/,"");
    const selected:SecondPilotProduct[]=[...pairs].map(([pid,observations])=>{
      // Preserve one observation, never sum overlapping windows into a fictitious sales total.
      const observation=[...observations].sort((a,b)=>b.source.observedAt-a.source.observedAt||b.units-a.units||a.id.localeCompare(b.id))[0],product=byPid.get(pid)!;
      if(!ITALIAN_PRODUCT_NAMES[pid])throw new Error("Product has no grounded Italian draft label.");
      return {productId:product.id,pid,title:product.title,italianName:ITALIAN_PRODUCT_NAMES[pid],units:observation.units,source:observation.source};
    }).sort((a,b)=>b.units-a.units||(a.pid<b.pid?-1:a.pid>b.pid?1:0));
    if(selected.length>5)throw new Error("More than five observed products require an explicit multi-message plan, not silent omission.");
    const liveBlockers=[
      "Kalodata 研究身份尚未核验为 TikTok IM 收件 OEC；历史同名映射只作线索。",
      "真实关系控制和拒联记录未确认；无记录不代表允许联系。",
      "新系统未启用真实 TikTok IM 连接器、发送账号和本次授权范围。",
      "意大利批量发送能力未完成当前验收；本次仅运行本地模拟传输。",
    ];
    const draft=draftSecondIntro(handle,selected),sourceFingerprint=hash({datasetId,readinessFingerprint,creator,products:selected,observations:[...pairs.values()].flat().sort((a,b)=>a.id.localeCompare(b.id)),draft});
    cases.push({id:`second-it-${hash({datasetId,namespace:"kalodata",id:creator.externalIdentity.id}).slice(0,24)}`,sourceFingerprint,creatorId,creatorRef:creator.externalIdentity,handle,market:"it",products:selected,draft,liveBlockers});
  }
  if(!cases.length)throw new Error("No exact-PID second-pilot cases were produced.");
  return cases;
}
