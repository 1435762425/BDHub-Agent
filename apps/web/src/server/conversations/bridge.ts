import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {isLocalRequest} from "../runtime/validation.ts";
import {enabledMarket} from "../markets/registry.ts";

export type ConversationView="human"|"agent"|"completed"|"all";
export type ConversationItem={conversationId:string|null;creatorId:string;oec:string;handle:string|null;state:Exclude<ConversationView,"all">;humanReason:string|null;humanReasonLabel:string|null;latestText:string|null;latestAt:number;waitingSeconds:number;unread:boolean;action:string|null;caseId:string|null};
export type ConversationList={available:true;view:ConversationView;query:string;counts:Record<ConversationView,number>;total:number;offset:number;limit:number;nextOffset:number|null;items:ConversationItem[];platformWrites:0;realSends:0};
export type TimelineItem={id:string;direction:"inbound"|"outbound";kind:string;text:string|null;occurredAt:number;status:string;source:string;pid?:string;listId?:string};
export type ManualTemplate={id:string;name:string;category:string;body:string;revision:number;state:"active"|"archived"};
export type CreatorMetrics={gmv:string|number|null;videoGmv:string|number|null;liveGmv:string|number|null;followers:number|null;unitsSold:number|null;avgVideoViews:number|null;observedAt:string|null;replyCount:number;showcaseCount:number};
export type CollaborationStatus="normal"|"collaborated"|"paid"|"rejected";
export type ConversationDetail={available:true;conversationId:string;creator:{creatorId:string;oec:string;handle:string|null;mode:string;rejected:boolean;unlocked:boolean;revision:number;collaboration:{status:CollaborationStatus;source:"manual"|"auto";revision:number;updatedAt:number}};timeline:TimelineItem[];episodes:Array<{episodeId:string;pid:string;listId:string;sentAt:number}>;case:{id:string;reason:string;reasonLabel:string;createdAt:number;revision:number;virtual:boolean;turnId:string|null;pendingRevision:number}|null;metrics:CreatorMetrics;manualReply:{id:string;kind:"manual"|"manual_card";confirmedAt:number}|null;draft:{text:string;revision:number;updatedAt:number};manualTemplates:ManualTemplate[];platformWrites:0;realSends:0};

export type ConversationCommand=
 | {action:"save_draft";market:string;cid:string;text:string;expectedRevision:number}
 | {action:"send_text";market:string;cid:string;text:string;expectedControlRevision:number;requestId:string}
 | {action:"send_card";market:string;cid:string;episodeId:string;expectedControlRevision:number;requestId:string}
 | {action:"complete_human";market:string;cid:string;turnId:string;expectedControlRevision:number;expectedPendingRevision:number;note:string}
 | {action:"confirm_manual_reply";market:string;cid:string;caseId:string;turnId:string|null;virtual:boolean;expectedControlRevision:number;expectedPendingRevision:number}
 | {action:"reject_creator";market:string;cid:string;expectedControlRevision:number;requestId:string}
 | {action:"set_collaboration";market:string;cid:string;status:CollaborationStatus;expectedStatusRevision:number;expectedControlRevision:number;requestId:string}
 | {action:"translate";market:string;text:string;target:string};

function run(args:string[],stdin?:unknown,market="it"):Promise<unknown>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  const child=execFile(join(root,".venv/bin/python"),[join(root,"scripts/conversation-workbench.py"),...args,"--market",market],
   {cwd:root,timeout:120000,maxBuffer:4*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},
   (error,stdout)=>{try{const value=JSON.parse(stdout);if(error||value.error)throw Error();resolve(value);}catch{reject(Error("conversation_workbench_unavailable"));}});
  if(stdin!==undefined)child.stdin?.end(JSON.stringify(stdin));
 });
}

const number=(value:unknown,max=Number.MAX_SAFE_INTEGER)=>typeof value==="number"&&Number.isSafeInteger(value)&&value>=0&&value<=max?value:(()=>{throw Error("invalid_conversation");})();
const stamp=(value:unknown)=>typeof value==="number"&&Number.isFinite(value)&&value>=0?value:(()=>{throw Error("invalid_conversation");})();
const text=(value:unknown,max:number,optional=false)=>value==null&&optional?null:typeof value==="string"&&value.length<=max?value:(()=>{throw Error("invalid_conversation");})();
const exact=(value:Record<string,unknown>,keys:string[])=>{if(Object.keys(value).sort().join(",")!==[...keys].sort().join(","))throw Error("invalid_conversation_request");};
const identifier=(value:unknown,max:number)=>{const clean=text(value,max);if(!clean?.trim())throw Error("invalid_conversation");return clean;};

export function validateConversationList(raw:unknown):ConversationList{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("invalid_conversation");
 const value=raw as Record<string,unknown>;
 if(value.available!==true||value.platformWrites!==0||value.realSends!==0||!Array.isArray(value.items)||value.items.length>100)throw Error("invalid_conversation");
 const view=value.view as ConversationView;
 if(!["human","agent","completed","all"].includes(view))throw Error("invalid_conversation");
 const items:ConversationItem[]=value.items.map(rawItem=>{
  if(!rawItem||typeof rawItem!=="object"||Array.isArray(rawItem))throw Error("invalid_conversation");
  const row=rawItem as Record<string,unknown>,state=row.state;
  if(!["human","agent","completed"].includes(String(state))||typeof row.unread!=="boolean")throw Error("invalid_conversation");
  return {conversationId:text(row.conversationId,40,true),creatorId:identifier(row.creatorId,120),oec:identifier(row.oec,40),handle:text(row.handle,100,true),state:state as ConversationItem["state"],humanReason:text(row.humanReason,80,true),humanReasonLabel:text(row.humanReasonLabel,120,true),latestText:text(row.latestText,4000,true),latestAt:stamp(row.latestAt),waitingSeconds:number(row.waitingSeconds),unread:row.unread,action:text(row.action,40,true),caseId:text(row.caseId,80,true)};
 });
 const rawCounts=value.counts;
 if(!rawCounts||typeof rawCounts!=="object"||Array.isArray(rawCounts)||Object.keys(rawCounts).sort().join(",")!=="agent,all,completed,human")throw Error("invalid_conversation");
 const counts=Object.fromEntries(Object.entries(rawCounts).map(([key,count])=>[key,number(count)])) as Record<ConversationView,number>;
 const total=number(value.total),offset=number(value.offset,5000),limit=number(value.limit,100),nextOffset=value.nextOffset==null?null:number(value.nextOffset,5100);
 if(limit<1||items.length>limit||total!==counts[view]||(nextOffset==null)!==(offset+items.length>=total))throw Error("invalid_conversation");
 return {available:true,view,query:text(value.query,100)??"",counts,total,offset,limit,nextOffset,items,platformWrites:0,realSends:0};
}

export function validateConversationDetail(raw:unknown):ConversationDetail{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("invalid_conversation");
 const value=raw as Record<string,unknown>;
 if(value.available!==true||value.platformWrites!==0||value.realSends!==0||!Array.isArray(value.timeline)||value.timeline.length>2000||!Array.isArray(value.episodes)||value.episodes.length>100||!Array.isArray(value.manualTemplates)||value.manualTemplates.length>500)throw Error("invalid_conversation");
 const rawCreator=value.creator;
 if(!rawCreator||typeof rawCreator!=="object"||Array.isArray(rawCreator))throw Error("invalid_conversation");
 const creator=rawCreator as Record<string,unknown>;
 if(typeof creator.rejected!=="boolean"||typeof creator.unlocked!=="boolean")throw Error("invalid_conversation");
 const timeline:TimelineItem[]=value.timeline.map(rawItem=>{
  if(!rawItem||typeof rawItem!=="object"||Array.isArray(rawItem))throw Error("invalid_conversation");
  const row=rawItem as Record<string,unknown>;
  if(row.direction!=="inbound"&&row.direction!=="outbound")throw Error("invalid_conversation");
  return {id:identifier(row.id,120),direction:row.direction,kind:identifier(row.kind,40),text:text(row.text,4000,true),occurredAt:stamp(row.occurredAt),status:identifier(row.status,40),source:identifier(row.source,40),...(row.pid!=null?{pid:identifier(row.pid,40)}:{}),...(row.listId!=null?{listId:identifier(row.listId,64)}:{})};
 });
 const episodes=(value.episodes as unknown[]).map(rawEpisode=>{if(!rawEpisode||typeof rawEpisode!=="object"||Array.isArray(rawEpisode))throw Error("invalid_conversation");const row=rawEpisode as Record<string,unknown>;return {episodeId:identifier(row.episodeId,120),pid:identifier(row.pid,40),listId:identifier(row.listId,64),sentAt:stamp(row.sentAt)};});
 let caseValue:ConversationDetail["case"]=null;
 if(value.case!=null){if(typeof value.case!=="object"||Array.isArray(value.case))throw Error("invalid_conversation");const row=value.case as Record<string,unknown>;if(typeof row.virtual!=="boolean")throw Error("invalid_conversation");caseValue={id:identifier(row.id,80),reason:identifier(row.reason,80),reasonLabel:identifier(row.reasonLabel,120),createdAt:stamp(row.createdAt),revision:number(row.revision),virtual:row.virtual,turnId:text(row.turnId,40,true),pendingRevision:number(row.pendingRevision)};if(caseValue.virtual&&!caseValue.turnId?.match(/^turn-[a-f0-9]{24}$/))throw Error("invalid_conversation");}
 if(!value.draft||typeof value.draft!=="object"||Array.isArray(value.draft))throw Error("invalid_conversation");
 const rawDraft=value.draft as Record<string,unknown>,draft={text:text(rawDraft.text,4000)??"",revision:number(rawDraft.revision),updatedAt:stamp(rawDraft.updatedAt)};
 const manualTemplates:ManualTemplate[]=(value.manualTemplates as unknown[]).map(rawTemplate=>{if(!rawTemplate||typeof rawTemplate!=="object"||Array.isArray(rawTemplate))throw Error("invalid_conversation");const row=rawTemplate as Record<string,unknown>;if(row.state!=="active"&&row.state!=="archived")throw Error("invalid_conversation");return {id:identifier(row.id,40),name:identifier(row.name,60),category:identifier(row.category,60),body:identifier(row.body,2000),revision:number(row.revision),state:row.state};});
 if(!value.metrics||typeof value.metrics!=="object"||Array.isArray(value.metrics))throw Error("invalid_conversation");const rawMetrics=value.metrics as Record<string,unknown>;
 const metric=(raw:unknown)=>raw==null?null:typeof raw==="string"&&raw.length<=128?raw:typeof raw==="number"&&Number.isFinite(raw)&&raw>=0?raw:(()=>{throw Error("invalid_conversation");})();
 const countMetric=(raw:unknown)=>raw==null?null:number(raw);
 const metrics:CreatorMetrics={gmv:metric(rawMetrics.gmv),videoGmv:metric(rawMetrics.videoGmv),liveGmv:metric(rawMetrics.liveGmv),followers:countMetric(rawMetrics.followers),unitsSold:countMetric(rawMetrics.unitsSold),avgVideoViews:countMetric(rawMetrics.avgVideoViews),observedAt:text(rawMetrics.observedAt,64,true),replyCount:number(rawMetrics.replyCount),showcaseCount:number(rawMetrics.showcaseCount)};
 let manualReply:ConversationDetail["manualReply"]=null;if(value.manualReply!=null){if(typeof value.manualReply!=="object"||Array.isArray(value.manualReply))throw Error("invalid_conversation");const row=value.manualReply as Record<string,unknown>;if(row.kind!=="manual"&&row.kind!=="manual_card")throw Error("invalid_conversation");manualReply={id:identifier(row.id,80),kind:row.kind,confirmedAt:stamp(row.confirmedAt)};}
 const collaborationRaw=creator.collaboration;if(!collaborationRaw||typeof collaborationRaw!=="object"||Array.isArray(collaborationRaw))throw Error("invalid_conversation");const collaboration=collaborationRaw as Record<string,unknown>;if(!["normal","collaborated","paid","rejected"].includes(String(collaboration.status))||(collaboration.source!=="manual"&&collaboration.source!=="auto"))throw Error("invalid_conversation");
 return {available:true,conversationId:identifier(value.conversationId,40),creator:{creatorId:identifier(creator.creatorId,120),oec:identifier(creator.oec,40),handle:text(creator.handle,100,true),mode:identifier(creator.mode,30),rejected:creator.rejected,unlocked:creator.unlocked,revision:number(creator.revision),collaboration:{status:collaboration.status as CollaborationStatus,source:collaboration.source,revision:number(collaboration.revision),updatedAt:stamp(collaboration.updatedAt)}},timeline,episodes,case:caseValue,metrics,manualReply,draft,manualTemplates,platformWrites:0,realSends:0};
}

export function validateConversationCommand(raw:unknown):ConversationCommand{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("invalid_conversation_request");
 const value=raw as Record<string,unknown>,action=value.action;
 const market=()=>{const result=identifier(value.market,2),record=enabledMarket(result);if(!record)throw Error("invalid_conversation_request");return result;};
 const cid=()=>{const result=identifier(value.cid,40);if(!/^\d{1,40}$/.test(result))throw Error("invalid_conversation_request");return result;};
 const requestId=()=>{const result=identifier(value.requestId,120);if(!/^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$/.test(result))throw Error("invalid_conversation_request");return result;};
 if(action==="translate"){exact(value,["action","market","target","text"]);const body=text(value.text,4000),selected=market(),target=identifier(value.target,2),targetRecord=target==="zh"?null:enabledMarket(target);if(!body?.trim()||target!=="zh"&&(!targetRecord||!targetRecord.contentReady))throw Error("invalid_conversation_request");return {action,market:selected,text:body,target};}
 if(action==="save_draft"){exact(value,["action","market","cid","expectedRevision","text"]);return {action,market:market(),cid:cid(),text:text(value.text,4000)??"",expectedRevision:number(value.expectedRevision,1_000_000)};}
 if(action==="send_text"){exact(value,["action","market","cid","expectedControlRevision","requestId","text"]);const body=text(value.text,4000);const revision=number(value.expectedControlRevision,1_000_000);if(!body?.trim()||revision<1)throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),text:body,expectedControlRevision:revision,requestId:requestId()};}
 if(action==="send_card"){exact(value,["action","market","cid","episodeId","expectedControlRevision","requestId"]);const episodeId=identifier(value.episodeId,40),revision=number(value.expectedControlRevision,1_000_000);if(!/^episode-[a-f0-9]{24}$/.test(episodeId)||revision<1)throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),episodeId,expectedControlRevision:revision,requestId:requestId()};}
 if(action==="complete_human"){exact(value,["action","market","cid","expectedControlRevision","expectedPendingRevision","note","turnId"]);const turnId=identifier(value.turnId,40),control=number(value.expectedControlRevision,1_000_000),pending=number(value.expectedPendingRevision,1_000_000),note=text(value.note,4000);if(!/^turn-[a-f0-9]{24}$/.test(turnId)||control<1||pending<1||!note?.trim())throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),turnId,expectedControlRevision:control,expectedPendingRevision:pending,note};}
 if(action==="confirm_manual_reply"){exact(value,["action","market","caseId","cid","expectedControlRevision","expectedPendingRevision","turnId","virtual"]);const caseId=identifier(value.caseId,80),control=number(value.expectedControlRevision,1_000_000),pending=number(value.expectedPendingRevision,1_000_000),turnId=text(value.turnId,40,true);if(typeof value.virtual!=="boolean"||control<1||pending<1||!/^case-[a-f0-9]{24}$|^review-[a-f0-9]{24}$/.test(caseId)||(value.virtual?!turnId?.match(/^turn-[a-f0-9]{24}$/):turnId!==null))throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),caseId,turnId,virtual:value.virtual,expectedControlRevision:control,expectedPendingRevision:pending};}
 if(action==="reject_creator"){exact(value,["action","market","cid","expectedControlRevision","requestId"]);const control=number(value.expectedControlRevision,1_000_000);if(control<1)throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),expectedControlRevision:control,requestId:requestId()};}
 if(action==="set_collaboration"){exact(value,["action","market","cid","expectedControlRevision","expectedStatusRevision","requestId","status"]);const control=number(value.expectedControlRevision,1_000_000),statusRevision=number(value.expectedStatusRevision,1_000_000);if(control<1||!["normal","collaborated","paid","rejected"].includes(String(value.status)))throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),status:value.status as CollaborationStatus,expectedStatusRevision:statusRevision,expectedControlRevision:control,requestId:requestId()};}
 throw Error("invalid_conversation_request");
}

export async function listConversations(view:ConversationView,query:string,limit:number,offset:number,market:string){return validateConversationList(await run(["list","--view",view,"--query",query,"--limit",String(limit),"--offset",String(offset)],undefined,market));}
export async function readConversation(cid:string,market:string){return validateConversationDetail(await run(["detail","--cid",cid],undefined,market));}
export function saveConversationDraft(market:string,cid:string,textValue:string,expectedRevision:number){return run(["save-draft","--cid",cid],{text:textValue,expectedRevision},market);}
export function sendConversationText(market:string,cid:string,textValue:string,expectedControlRevision:number,requestId:string){return run(["send-text","--cid",cid],{text:textValue,expectedControlRevision,requestId},market);}
export function translateConversationText(market:string,textValue:string,target:string){return run(["translate"],{text:textValue,target},market);}
export function sendConversationCard(market:string,cid:string,episodeId:string,expectedControlRevision:number,requestId:string){return run(["send-card","--cid",cid],{episodeId,expectedControlRevision,requestId},market);}
export function completeReviewedHuman(market:string,cid:string,turnId:string,expectedControlRevision:number,expectedPendingRevision:number,note:string){return run(["complete-human","--cid",cid],{turnId,expectedControlRevision,expectedPendingRevision,note},market);}
export function confirmManualReply(market:string,cid:string,caseId:string,turnId:string|null,virtual:boolean,expectedControlRevision:number,expectedPendingRevision:number){return run(["confirm-manual","--cid",cid],{caseId,turnId,virtual,expectedControlRevision,expectedPendingRevision},market);}
export function rejectConversationCreator(market:string,cid:string,expectedControlRevision:number,requestId:string){return run(["reject-creator","--cid",cid],{expectedControlRevision,requestId},market);}
export function setConversationCollaboration(market:string,cid:string,status:CollaborationStatus,expectedStatusRevision:number,expectedControlRevision:number,requestId:string){return run(["set-collaboration","--cid",cid],{status,expectedStatusRevision,expectedControlRevision,requestId},market);}

async function jsonBody(request:Request,maxBytes=20_000):Promise<unknown>{
 if(request.headers.get("content-type")?.split(";")[0].trim()!=="application/json")throw Error("json_required");
 const reader=request.body?.getReader();if(!reader)throw Error("invalid_conversation_request");
 const decoder=new TextDecoder("utf-8",{fatal:true});let raw="",size=0;
 try{for(;;){const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>maxBytes){await reader.cancel();throw Error("invalid_conversation_request");}raw+=decoder.decode(part.value,{stream:true});}raw+=decoder.decode();}
 finally{reader.releaseLock();}
 return JSON.parse(raw);
}

type ConversationOperations={
 list:typeof listConversations;detail:typeof readConversation;saveDraft:typeof saveConversationDraft;
 sendText:typeof sendConversationText;sendCard:typeof sendConversationCard;translate:typeof translateConversationText;completeHuman:typeof completeReviewedHuman;confirmManual:typeof confirmManualReply;rejectCreator:typeof rejectConversationCreator;setCollaboration:typeof setConversationCollaboration;
};
const defaults:ConversationOperations={list:listConversations,detail:readConversation,saveDraft:saveConversationDraft,sendText:sendConversationText,sendCard:sendConversationCard,translate:translateConversationText,completeHuman:completeReviewedHuman,confirmManual:confirmManualReply,rejectCreator:rejectConversationCreator,setCollaboration:setConversationCollaboration};
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export function createConversationHandlers(operations:ConversationOperations=defaults){return {
 GET:async(request:Request)=>{
  if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});
  let query:{cid?:string;view?:ConversationView;text?:string;limit?:number;offset?:number;market:string};
  try{const params=new URL(request.url).searchParams;const allowed=new Set(["view","query","limit","offset","cid","market"]);if([...params.keys()].some(key=>!allowed.has(key)||params.getAll(key).length!==1)||params.getAll("market").length!==1)throw Error();const selected=params.get("market")!;if(!enabledMarket(selected))throw Error();const cid=params.get("cid");if(cid!=null){if([...params.keys()].some(key=>!['cid','market'].includes(key))||!/^\d{1,40}$/.test(cid))throw Error();query={cid,market:selected};}else{const view=(params.get("view")??"human") as ConversationView;if(!["human","agent","completed","all"].includes(view))throw Error();const textValue=params.get("query")??"";if(textValue.length>100)throw Error();const bounded=(key:string,fallback:number,low:number,max:number)=>{const raw=params.get(key);if(raw==null)return fallback;if(!/^\d+$/.test(raw))throw Error();const value=Number(raw);if(!Number.isSafeInteger(value)||value<low||value>max)throw Error();return value;};query={view,text:textValue,limit:bounded("limit",30,1,100),offset:bounded("offset",0,0,5000),market:selected};}}
  catch{return Response.json({error:"invalid_conversation_query"},{status:400,headers});}
  try{return Response.json(query.cid?await operations.detail(query.cid,query.market):await operations.list(query.view!,query.text!,query.limit!,query.offset!,query.market),{headers});}
  catch{return Response.json({error:"conversation_workbench_unavailable"},{status:503,headers});}
 },
 POST:async(request:Request)=>{
  if(!isLocalRequest(request,true))return Response.json({error:"local_origin_required"},{status:403,headers});
  let command:ConversationCommand;let selected="";
  try{const url=new URL(request.url);if([...url.searchParams.keys()].some(key=>key!=="market")||url.searchParams.getAll("market").length!==1)throw Error();selected=url.searchParams.get("market")!;if(!enabledMarket(selected))throw Error();command=validateConversationCommand(await jsonBody(request));}
  catch(error){const status=error instanceof Error&&error.message==="json_required"?415:400;return Response.json({error:status===415?"json_required":"invalid_conversation_request"},{status,headers});}
  try{
   if(command.market!==selected)return Response.json({error:"market_mismatch"},{status:409,headers});
   const record=enabledMarket(selected)!;if(record.runtimeState==="planned")return Response.json({error:"market_runtime_unavailable"},{status:409,headers});
   if(selected!=="it"&&(command.action==="send_text"||command.action==="send_card"))return Response.json({error:"market_conversation_send_pending"},{status:409,headers});
   const result=command.action==="translate"?await operations.translate(selected,command.text,command.target):
    command.action==="save_draft"?await operations.saveDraft(selected,command.cid,command.text,command.expectedRevision):
    command.action==="send_text"?await operations.sendText(selected,command.cid,command.text,command.expectedControlRevision,command.requestId):
    command.action==="complete_human"?await operations.completeHuman(selected,command.cid,command.turnId,command.expectedControlRevision,command.expectedPendingRevision,command.note):
    command.action==="confirm_manual_reply"?await operations.confirmManual(selected,command.cid,command.caseId,command.turnId,command.virtual,command.expectedControlRevision,command.expectedPendingRevision):
    command.action==="set_collaboration"?await operations.setCollaboration(selected,command.cid,command.status,command.expectedStatusRevision,command.expectedControlRevision,command.requestId):
    command.action==="reject_creator"?await operations.rejectCreator(selected,command.cid,command.expectedControlRevision,command.requestId):
    await operations.sendCard(selected,command.cid,command.episodeId,command.expectedControlRevision,command.requestId);
   return Response.json(result,{headers});
  }catch{return Response.json({error:"conversation_workbench_unavailable"},{status:503,headers});}
 }
};}
