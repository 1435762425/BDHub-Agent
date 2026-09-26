import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../runtime/project-root.ts";
import {isLocalRequest} from "../runtime/validation.ts";
import {enabledMarket} from "../markets/registry.ts";

export type ConversationView="human"|"technical"|"agent"|"waiting"|"completed"|"all";
export const CONVERSATION_VIEWS:ConversationView[]=["human","technical","agent","waiting","completed","all"];
/** Shared byte contract with scripts/conversation-workbench.py MAX_INPUT_BYTES. */
export const MAX_BODY_BYTES=32768;
/** "occurredAt|itemId" of the oldest item on a timeline page. */
export const TIMELINE_CURSOR=/^\d+(?:\.\d+)?(?:e[-+]?\d+)?\|[^|]{1,120}$/;
export type ConversationItem={conversationId:string|null;creatorId:string;oec:string;handle:string|null;state:Exclude<ConversationView,"all">;queueStatusLabel:string;humanReason:string|null;humanReasonLabel:string|null;latestText:string|null;latestMeaningZh:string|null;latestAt:number;waitingSeconds:number;unread:boolean;action:string|null;caseId:string|null};
export type ConversationList={available:true;view:ConversationView;query:string;counts:Record<ConversationView,number>;total:number;offset:number;limit:number;nextOffset:number|null;items:ConversationItem[];platformWrites:0;realSends:0};
export type TimelineItem={id:string;direction:"inbound"|"outbound";kind:string;text:string|null;occurredAt:number;status:string;source:string;pid?:string;listId?:string};
export type ManualTemplate={id:string;name:string;category:string;body:string;revision:number;state:"active"|"archived"};
export type CreatorMetrics={gmv:string|number|null;videoGmv:string|number|null;liveGmv:string|number|null;followers:number|null;unitsSold:number|null;avgVideoViews:number|null;observedAt:string|null;replyCount:number;showcaseCount:number};
export type CollaborationStatus="normal"|"collaborated"|"paid"|"rejected";
export type ReplyIntentState="ready"|"inflight"|"accepted"|"unknown"|"isolated";
export type PendingManualReply={id:string|null;kind:"manual"|"manual_card";requestId:string;state:ReplyIntentState};
export type HeldReply={id:string;kind:string;requestId:string;state:"inflight"|"accepted"|"unknown"|"isolated";startedAt:number|null;senderAccount:string|null;isolationReason:string|null;deadlineAt:number|null};
/** Whether a failed send could have reached the platform. Only absent/not_submitted may be cleared. */
export type IntentOutcome="absent"|"not_submitted"|"unresolved"|"unknown";
export class ConversationError extends Error{intent:IntentOutcome;code:string;state?:string;constructor(code:string,intent:IntentOutcome,state?:string){super(code);this.code=code;this.intent=intent;this.state=state;}}
export type ConversationDetail={available:true;conversationId:string;latestTurnId:string|null;creator:{creatorId:string;oec:string;handle:string|null;mode:string;rejected:boolean;unlocked:boolean;revision:number;collaboration:{status:CollaborationStatus;source:"manual"|"auto";revision:number;updatedAt:number}};timeline:TimelineItem[];timelineHasOlder:boolean;timelineCursor:string|null;episodes:Array<{episodeId:string;pid:string;listId:string;sentAt:number}>;case:{id:string;reason:string;reasonLabel:string;createdAt:number;revision:number;virtual:boolean;turnId:string|null;pendingRevision:number}|null;agentDecisions:Array<{decisionId:string;guideRevision:number;route:string|null;reasonZh:string|null;state:string;serviceReplyId:string|null;createdAt:number}>;metrics:CreatorMetrics;manualReply:{id:string;kind:"manual"|"manual_card";confirmedAt:number}|null;pendingManualReplies:PendingManualReply[];heldReplies:HeldReply[];technicalHold:{reason:string;label:string}|null;draft:{text:string;revision:number;updatedAt:number};manualTemplates:ManualTemplate[];platformWrites:0;realSends:0};

export type ConversationCommand=
 | {action:"save_draft";market:string;cid:string;text:string;expectedRevision:number}
 | {action:"send_text";market:string;cid:string;text:string;expectedControlRevision:number;requestId:string}
 | {action:"send_card";market:string;cid:string;episodeId:string;expectedControlRevision:number;requestId:string}
 | {action:"reconcile_manual";market:string;cid:string;requestId:string}
 | {action:"complete_human";market:string;cid:string;turnId:string;expectedControlRevision:number;expectedPendingRevision:number;note:string}
 | {action:"confirm_manual_reply";market:string;cid:string;caseId:string;turnId:string|null;virtual:boolean;expectedControlRevision:number;expectedPendingRevision:number}
 | {action:"resolve_manual";market:string;cid:string;caseId:string;latestTurnId:string;outcome:"normal"|"paid"|"rejected";expectedControlRevision:number;expectedPendingRevision:number;expectedStatusRevision:number;requestId:string}
 | {action:"reject_creator";market:string;cid:string;expectedControlRevision:number;requestId:string}
 | {action:"set_collaboration";market:string;cid:string;status:CollaborationStatus;expectedStatusRevision:number;expectedControlRevision:number;requestId:string}
 | {action:"translate";market:string;text:string;target:string};

function run(args:string[],stdin?:unknown,market="it"):Promise<unknown>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  const child=execFile(join(root,".venv/bin/python"),[join(root,"scripts/conversation-workbench.py"),...args,"--market",market],
   {cwd:root,timeout:120000,maxBuffer:4*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},
   (error,stdout)=>{let value:Record<string,unknown>|null=null;try{value=JSON.parse(stdout);}catch{value=null;}
    if(!error&&value&&!value.error){resolve(value);return;}
    const intent=value&&["absent","not_submitted","unresolved","unknown"].includes(String(value.intent))?value.intent as IntentOutcome:"unknown";
    reject(new ConversationError(typeof value?.error==="string"?value.error.slice(0,120):"conversation_workbench_unavailable",intent,typeof value?.state==="string"?value.state:undefined));});
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
 if(!CONVERSATION_VIEWS.includes(view))throw Error("invalid_conversation");
 const items:ConversationItem[]=value.items.map(rawItem=>{
  if(!rawItem||typeof rawItem!=="object"||Array.isArray(rawItem))throw Error("invalid_conversation");
  const row=rawItem as Record<string,unknown>,state=row.state;
  if(!CONVERSATION_VIEWS.includes(state as ConversationView)||state==="all"||typeof row.unread!=="boolean")throw Error("invalid_conversation");
  return {conversationId:text(row.conversationId,40,true),creatorId:identifier(row.creatorId,120),oec:identifier(row.oec,40),handle:text(row.handle,100,true),state:state as ConversationItem["state"],queueStatusLabel:identifier(row.queueStatusLabel,80),humanReason:text(row.humanReason,80,true),humanReasonLabel:text(row.humanReasonLabel,120,true),latestText:text(row.latestText,4000,true),latestMeaningZh:text(row.latestMeaningZh,500,true),latestAt:stamp(row.latestAt),waitingSeconds:number(row.waitingSeconds),unread:row.unread,action:text(row.action,40,true),caseId:text(row.caseId,80,true)};
 });
 const rawCounts=value.counts;
 if(!rawCounts||typeof rawCounts!=="object"||Array.isArray(rawCounts)||Object.keys(rawCounts).sort().join(",")!==[...CONVERSATION_VIEWS].sort().join(","))throw Error("invalid_conversation");
 const counts=Object.fromEntries(Object.entries(rawCounts).map(([key,count])=>[key,number(count)])) as Record<ConversationView,number>;
 const total=number(value.total),offset=number(value.offset,5000),limit=number(value.limit,100),nextOffset=value.nextOffset==null?null:number(value.nextOffset,5100);
 if(limit<1||items.length>limit||total!==counts[view]||(nextOffset==null)!==(offset+items.length>=total))throw Error("invalid_conversation");
 return {available:true,view,query:text(value.query,100)??"",counts,total,offset,limit,nextOffset,items,platformWrites:0,realSends:0};
}

export function validateConversationDetail(raw:unknown):ConversationDetail{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("invalid_conversation");
 const value=raw as Record<string,unknown>;
 if(value.available!==true||value.platformWrites!==0||value.realSends!==0||!Array.isArray(value.timeline)||value.timeline.length>2000||typeof value.timelineHasOlder!=="boolean"||(value.timelineCursor!=null&&!TIMELINE_CURSOR.test(String(value.timelineCursor)))||value.timelineHasOlder!==(value.timelineCursor!=null)||!Array.isArray(value.episodes)||value.episodes.length>100||!Array.isArray(value.manualTemplates)||value.manualTemplates.length>500)throw Error("invalid_conversation");
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
 if(!Array.isArray(value.agentDecisions)||value.agentDecisions.length>12)throw Error("invalid_conversation");
 const agentDecisions=value.agentDecisions.map(raw=>{if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("invalid_conversation");const row=raw as Record<string,unknown>;const decisionId=identifier(row.decisionId,80);if(!/^agent-decision-[a-f0-9]{24}$/.test(decisionId))throw Error("invalid_conversation");return {decisionId,guideRevision:number(row.guideRevision),route:text(row.route,30,true),reasonZh:text(row.reasonZh,500,true),state:identifier(row.state,30),serviceReplyId:text(row.serviceReplyId,80,true),createdAt:stamp(row.createdAt)};});
 if(!value.metrics||typeof value.metrics!=="object"||Array.isArray(value.metrics))throw Error("invalid_conversation");const rawMetrics=value.metrics as Record<string,unknown>;
 const metric=(raw:unknown)=>raw==null?null:typeof raw==="string"&&raw.length<=128?raw:typeof raw==="number"&&Number.isFinite(raw)&&raw>=0?raw:(()=>{throw Error("invalid_conversation");})();
 const countMetric=(raw:unknown)=>raw==null?null:number(raw);
 const metrics:CreatorMetrics={gmv:metric(rawMetrics.gmv),videoGmv:metric(rawMetrics.videoGmv),liveGmv:metric(rawMetrics.liveGmv),followers:countMetric(rawMetrics.followers),unitsSold:countMetric(rawMetrics.unitsSold),avgVideoViews:countMetric(rawMetrics.avgVideoViews),observedAt:text(rawMetrics.observedAt,64,true),replyCount:number(rawMetrics.replyCount),showcaseCount:number(rawMetrics.showcaseCount)};
 let manualReply:ConversationDetail["manualReply"]=null;if(value.manualReply!=null){if(typeof value.manualReply!=="object"||Array.isArray(value.manualReply))throw Error("invalid_conversation");const row=value.manualReply as Record<string,unknown>;if(row.kind!=="manual"&&row.kind!=="manual_card")throw Error("invalid_conversation");manualReply={id:identifier(row.id,80),kind:row.kind,confirmedAt:stamp(row.confirmedAt)};}
 if(!Array.isArray(value.pendingManualReplies)||value.pendingManualReplies.length>100)throw Error("invalid_conversation");
 const pendingManualReplies:PendingManualReply[]=value.pendingManualReplies.map(raw=>{
  if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("invalid_conversation");
  const row=raw as Record<string,unknown>;
  if(!["manual","manual_card"].includes(String(row.kind))||!["ready","inflight","accepted","unknown","isolated"].includes(String(row.state))||
   typeof row.requestId!=="string"||!/^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$/.test(row.requestId))throw Error("invalid_conversation");
  return {id:identifier(row.id,80),kind:row.kind as PendingManualReply["kind"],requestId:row.requestId,state:row.state as PendingManualReply["state"]};
 });
 if(!Array.isArray(value.heldReplies)||value.heldReplies.length>20)throw Error("invalid_conversation");
 const heldReplies:HeldReply[]=value.heldReplies.map(raw=>{
  if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("invalid_conversation");
  const row=raw as Record<string,unknown>;
  if(!["inflight","accepted","unknown","isolated"].includes(String(row.state))||typeof row.requestId!=="string"||!/^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$/.test(row.requestId))throw Error("invalid_conversation");
  return {id:identifier(row.id,80),kind:identifier(row.kind,40),requestId:row.requestId,state:row.state as HeldReply["state"],startedAt:row.startedAt==null?null:stamp(row.startedAt),senderAccount:text(row.senderAccount,40,true),isolationReason:text(row.isolationReason,80,true),deadlineAt:row.deadlineAt==null?null:stamp(row.deadlineAt)};
 });
 let technicalHold:ConversationDetail["technicalHold"]=null;
 if(value.technicalHold!=null){if(typeof value.technicalHold!=="object"||Array.isArray(value.technicalHold))throw Error("invalid_conversation");const row=value.technicalHold as Record<string,unknown>;technicalHold={reason:identifier(row.reason,40),label:identifier(row.label,80)};}
 const collaborationRaw=creator.collaboration;if(!collaborationRaw||typeof collaborationRaw!=="object"||Array.isArray(collaborationRaw))throw Error("invalid_conversation");const collaboration=collaborationRaw as Record<string,unknown>;if(!["normal","collaborated","paid","rejected"].includes(String(collaboration.status))||(collaboration.source!=="manual"&&collaboration.source!=="auto"))throw Error("invalid_conversation");
 return {available:true,conversationId:identifier(value.conversationId,40),latestTurnId:value.latestTurnId==null?null:identifier(value.latestTurnId,40),creator:{creatorId:identifier(creator.creatorId,120),oec:identifier(creator.oec,40),handle:text(creator.handle,100,true),mode:identifier(creator.mode,30),rejected:creator.rejected,unlocked:creator.unlocked,revision:number(creator.revision),collaboration:{status:collaboration.status as CollaborationStatus,source:collaboration.source,revision:number(collaboration.revision),updatedAt:stamp(collaboration.updatedAt)}},timeline,timelineHasOlder:value.timelineHasOlder as boolean,timelineCursor:value.timelineCursor==null?null:String(value.timelineCursor),episodes,case:caseValue,agentDecisions,metrics,manualReply,pendingManualReplies,heldReplies,technicalHold,draft,manualTemplates,platformWrites:0,realSends:0};
}

export function validateConversationCommand(raw:unknown):ConversationCommand{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("invalid_conversation_request");
 const value=raw as Record<string,unknown>,action=value.action;
 const market=()=>{const result=identifier(value.market,2),record=enabledMarket(result);if(!record)throw Error("invalid_conversation_request");return result;};
 const cid=()=>{const result=identifier(value.cid,40);if(!/^\d{1,40}$/.test(result))throw Error("invalid_conversation_request");return result;};
 const requestId=()=>{const result=identifier(value.requestId,120);if(!/^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$/.test(result))throw Error("invalid_conversation_request");return result;};
 if(action==="reconcile_manual"){exact(value,["action","market","cid","requestId"]);return {action,market:market(),cid:cid(),requestId:requestId()};}
 if(action==="translate"){exact(value,["action","market","target","text"]);const body=text(value.text,4000),selected=market(),target=identifier(value.target,2),targetRecord=target==="zh"?null:enabledMarket(target);if(!body?.trim()||target!=="zh"&&(!targetRecord||!targetRecord.contentReady))throw Error("invalid_conversation_request");return {action,market:selected,text:body,target};}
 if(action==="save_draft"){exact(value,["action","market","cid","expectedRevision","text"]);return {action,market:market(),cid:cid(),text:text(value.text,4000)??"",expectedRevision:number(value.expectedRevision,1_000_000)};}
 if(action==="send_text"){exact(value,["action","market","cid","expectedControlRevision","requestId","text"]);const body=text(value.text,4000);const revision=number(value.expectedControlRevision,1_000_000);if(!body?.trim()||revision<1)throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),text:body,expectedControlRevision:revision,requestId:requestId()};}
 if(action==="send_card"){exact(value,["action","market","cid","episodeId","expectedControlRevision","requestId"]);const episodeId=identifier(value.episodeId,40),revision=number(value.expectedControlRevision,1_000_000);if(!/^episode-[a-f0-9]{24}$/.test(episodeId)||revision<1)throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),episodeId,expectedControlRevision:revision,requestId:requestId()};}
 if(action==="complete_human"){exact(value,["action","market","cid","expectedControlRevision","expectedPendingRevision","note","turnId"]);const turnId=identifier(value.turnId,40),control=number(value.expectedControlRevision,1_000_000),pending=number(value.expectedPendingRevision,1_000_000),note=text(value.note,4000);if(!/^turn-[a-f0-9]{24}$/.test(turnId)||control<1||pending<1||!note?.trim())throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),turnId,expectedControlRevision:control,expectedPendingRevision:pending,note};}
 if(action==="confirm_manual_reply"){exact(value,["action","market","caseId","cid","expectedControlRevision","expectedPendingRevision","turnId","virtual"]);const caseId=identifier(value.caseId,80),control=number(value.expectedControlRevision,1_000_000),pending=number(value.expectedPendingRevision,1_000_000),turnId=text(value.turnId,40,true);if(typeof value.virtual!=="boolean"||control<1||pending<1||!/^case-[a-f0-9]{24}$|^review-[a-f0-9]{24}$/.test(caseId)||(value.virtual?!turnId?.match(/^turn-[a-f0-9]{24}$/):turnId!==null))throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),caseId,turnId,virtual:value.virtual,expectedControlRevision:control,expectedPendingRevision:pending};}
 if(action==="resolve_manual"){exact(value,["action","market","caseId","cid","expectedControlRevision","expectedPendingRevision","expectedStatusRevision","latestTurnId","outcome","requestId"]);const caseId=identifier(value.caseId,80),latestTurnId=identifier(value.latestTurnId,40),outcome=value.outcome;if(!/^(case|review)-[a-f0-9]{24}$/.test(caseId)||!/^turn-[a-f0-9]{24}$/.test(latestTurnId)||!["normal","paid","rejected"].includes(String(outcome)))throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),caseId,latestTurnId,outcome:outcome as "normal"|"paid"|"rejected",expectedControlRevision:number(value.expectedControlRevision),expectedPendingRevision:number(value.expectedPendingRevision),expectedStatusRevision:number(value.expectedStatusRevision),requestId:requestId()};}
 if(action==="reject_creator"){exact(value,["action","market","cid","expectedControlRevision","requestId"]);const control=number(value.expectedControlRevision,1_000_000);if(control<1)throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),expectedControlRevision:control,requestId:requestId()};}
 if(action==="set_collaboration"){exact(value,["action","market","cid","expectedControlRevision","expectedStatusRevision","requestId","status"]);const control=number(value.expectedControlRevision,1_000_000),statusRevision=number(value.expectedStatusRevision,1_000_000);if(control<1||!["normal","collaborated","paid","rejected"].includes(String(value.status)))throw Error("invalid_conversation_request");return {action,market:market(),cid:cid(),status:value.status as CollaborationStatus,expectedStatusRevision:statusRevision,expectedControlRevision:control,requestId:requestId()};}
 throw Error("invalid_conversation_request");
}

export async function listConversations(view:ConversationView,query:string,limit:number,offset:number,market:string){return validateConversationList(await run(["list","--view",view,"--query",query,"--limit",String(limit),"--offset",String(offset)],undefined,market));}
export async function readConversation(cid:string,market:string,before?:string){return validateConversationDetail(await run(["detail","--cid",cid,...(before?["--before",before]:[])],undefined,market));}
export function saveConversationDraft(market:string,cid:string,textValue:string,expectedRevision:number){return run(["save-draft","--cid",cid],{text:textValue,expectedRevision},market);}
export async function sendConversationText(market:string,cid:string,textValue:string,expectedControlRevision:number,requestId:string){return validateManualReplyResult(await run(["send-text","--cid",cid],{text:textValue,expectedControlRevision,requestId},market),requestId);}
export function translateConversationText(market:string,textValue:string,target:string){return run(["translate"],{text:textValue,target},market);}
export async function sendConversationCard(market:string,cid:string,episodeId:string,expectedControlRevision:number,requestId:string){return validateManualReplyResult(await run(["send-card","--cid",cid],{episodeId,expectedControlRevision,requestId},market),requestId);}
export async function reconcileManualReply(market:string,cid:string,requestId:string){return validateManualReplyResult(await run(["reconcile-manual","--cid",cid],{requestId},market),requestId,true);}

export function validateManualReplyResult(raw:unknown,requestId:string,readOnly=false){
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("invalid_manual_reply_result");
 const value=raw as Record<string,unknown>;
 if(!["ready","inflight","accepted","unknown","isolated","confirmed","cancelled"].includes(String(value.state))||value.requestRef!==requestId||
  typeof value.replyId!=="string"||!/^(?:manual-(?:reply|card)|agent-reply|auto-reply)-[a-f0-9]{24}$/.test(value.replyId)||
  ![0,1].includes(value.platformWrites as number)||![0,1].includes(value.realSends as number)||
  readOnly&&(value.platformWrites!==0||value.realSends!==0))throw Error("invalid_manual_reply_result");
 return {state:String(value.state),replyId:value.replyId,requestRef:requestId,platformWrites:Number(value.platformWrites),realSends:Number(value.realSends)};
}
export function completeReviewedHuman(market:string,cid:string,turnId:string,expectedControlRevision:number,expectedPendingRevision:number,note:string){return run(["complete-human","--cid",cid],{turnId,expectedControlRevision,expectedPendingRevision,note},market);}
export function confirmManualReply(market:string,cid:string,caseId:string,turnId:string|null,virtual:boolean,expectedControlRevision:number,expectedPendingRevision:number){return run(["confirm-manual","--cid",cid],{caseId,turnId,virtual,expectedControlRevision,expectedPendingRevision},market);}
export function resolveManual(market:string,cid:string,caseId:string,latestTurnId:string,outcome:"normal"|"paid"|"rejected",expectedControlRevision:number,expectedPendingRevision:number,expectedStatusRevision:number,requestId:string){return run(["resolve-manual","--cid",cid],{caseId,latestTurnId,outcome,expectedControlRevision,expectedPendingRevision,expectedStatusRevision,requestId},market);}
export function rejectConversationCreator(market:string,cid:string,expectedControlRevision:number,requestId:string){return run(["reject-creator","--cid",cid],{expectedControlRevision,requestId},market);}
export function setConversationCollaboration(market:string,cid:string,status:CollaborationStatus,expectedStatusRevision:number,expectedControlRevision:number,requestId:string){return run(["set-collaboration","--cid",cid],{status,expectedStatusRevision,expectedControlRevision,requestId},market);}

async function jsonBody(request:Request,maxBytes=MAX_BODY_BYTES):Promise<unknown>{
 if(request.headers.get("content-type")?.split(";")[0].trim()!=="application/json")throw Error("json_required");
 const reader=request.body?.getReader();if(!reader)throw Error("invalid_conversation_request");
 const decoder=new TextDecoder("utf-8",{fatal:true});let raw="",size=0;
 try{for(;;){const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>maxBytes){await reader.cancel();throw Error("invalid_conversation_request");}raw+=decoder.decode(part.value,{stream:true});}raw+=decoder.decode();}
 finally{reader.releaseLock();}
 return JSON.parse(raw);
}

type ConversationOperations={
 list:typeof listConversations;detail:typeof readConversation;saveDraft:typeof saveConversationDraft;
 sendText:typeof sendConversationText;sendCard:typeof sendConversationCard;reconcileManual:typeof reconcileManualReply;translate:typeof translateConversationText;completeHuman:typeof completeReviewedHuman;confirmManual:typeof confirmManualReply;resolveManual:typeof resolveManual;rejectCreator:typeof rejectConversationCreator;setCollaboration:typeof setConversationCollaboration;
};
const defaults:ConversationOperations={list:listConversations,detail:readConversation,saveDraft:saveConversationDraft,sendText:sendConversationText,sendCard:sendConversationCard,reconcileManual:reconcileManualReply,translate:translateConversationText,completeHuman:completeReviewedHuman,confirmManual:confirmManualReply,resolveManual,rejectCreator:rejectConversationCreator,setCollaboration:setConversationCollaboration};
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export function createConversationHandlers(operations:ConversationOperations=defaults){return {
 GET:async(request:Request)=>{
  if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});
  let query:{cid?:string;before?:string;view?:ConversationView;text?:string;limit?:number;offset?:number;market:string};
  try{const params=new URL(request.url).searchParams;const allowed=new Set(["view","query","limit","offset","cid","market","before"]);if([...params.keys()].some(key=>!allowed.has(key)||params.getAll(key).length!==1)||params.getAll("market").length!==1)throw Error();const selected=params.get("market")!;if(!enabledMarket(selected))throw Error();const cid=params.get("cid");if(cid!=null){if([...params.keys()].some(key=>!['cid','market','before'].includes(key))||!/^\d{1,40}$/.test(cid))throw Error();const before=params.get("before");if(before!=null&&!TIMELINE_CURSOR.test(before))throw Error();query={cid,market:selected,...(before!=null?{before}:{})};}else{if(params.has("before"))throw Error();else{const view=(params.get("view")??"human") as ConversationView;if(!CONVERSATION_VIEWS.includes(view))throw Error();const textValue=params.get("query")??"";if(textValue.length>100)throw Error();const bounded=(key:string,fallback:number,low:number,max:number)=>{const raw=params.get(key);if(raw==null)return fallback;if(!/^\d+$/.test(raw))throw Error();const value=Number(raw);if(!Number.isSafeInteger(value)||value<low||value>max)throw Error();return value;};query={view,text:textValue,limit:bounded("limit",30,1,100),offset:bounded("offset",0,0,5000),market:selected};}}}
  catch{return Response.json({error:"invalid_conversation_query"},{status:400,headers});}
  try{return Response.json(query.cid?await operations.detail(query.cid,query.market,query.before):await operations.list(query.view!,query.text!,query.limit!,query.offset!,query.market),{headers});}
  catch{return Response.json({error:"conversation_workbench_unavailable"},{status:503,headers});}
 },
 POST:async(request:Request)=>{
  if(!isLocalRequest(request,true))return Response.json({error:"local_origin_required"},{status:403,headers});
  let command:ConversationCommand;let selected="";
  try{const url=new URL(request.url);if([...url.searchParams.keys()].some(key=>key!=="market")||url.searchParams.getAll("market").length!==1)throw Error();selected=url.searchParams.get("market")!;if(!enabledMarket(selected))throw Error();command=validateConversationCommand(await jsonBody(request));}
  catch(error){const status=error instanceof Error&&error.message==="json_required"?415:400;return Response.json({error:status===415?"json_required":"invalid_conversation_request",intent:"absent"},{status,headers});}
  try{
   if(command.market!==selected)return Response.json({error:"market_mismatch",intent:"absent"},{status:409,headers});
   const record=enabledMarket(selected)!;if(record.runtimeState==="planned")return Response.json({error:"market_runtime_unavailable",intent:"absent"},{status:409,headers});
   if(selected!=="it"&&(command.action==="send_text"||command.action==="send_card"))return Response.json({error:"market_conversation_send_pending",intent:"absent"},{status:409,headers});
   const result=command.action==="translate"?await operations.translate(selected,command.text,command.target):
    command.action==="reconcile_manual"?await operations.reconcileManual(selected,command.cid,command.requestId):
    command.action==="save_draft"?await operations.saveDraft(selected,command.cid,command.text,command.expectedRevision):
    command.action==="send_text"?await operations.sendText(selected,command.cid,command.text,command.expectedControlRevision,command.requestId):
    command.action==="complete_human"?await operations.completeHuman(selected,command.cid,command.turnId,command.expectedControlRevision,command.expectedPendingRevision,command.note):
    command.action==="confirm_manual_reply"?await operations.confirmManual(selected,command.cid,command.caseId,command.turnId,command.virtual,command.expectedControlRevision,command.expectedPendingRevision):
    command.action==="resolve_manual"?await operations.resolveManual(selected,command.cid,command.caseId,command.latestTurnId,command.outcome,command.expectedControlRevision,command.expectedPendingRevision,command.expectedStatusRevision,command.requestId):
    command.action==="set_collaboration"?await operations.setCollaboration(selected,command.cid,command.status,command.expectedStatusRevision,command.expectedControlRevision,command.requestId):
    command.action==="reject_creator"?await operations.rejectCreator(selected,command.cid,command.expectedControlRevision,command.requestId):
    await operations.sendCard(selected,command.cid,command.episodeId,command.expectedControlRevision,command.requestId);
   return Response.json(result,{headers});
  }catch(error){
   // A known CLI refusal carries whether the intent exists; anything else stays unknown.
   if(error instanceof ConversationError&&error.intent!=="unknown")return Response.json({error:error.code,intent:error.intent,...(error.state?{state:error.state}:{})},{status:409,headers});
   return Response.json({error:"conversation_workbench_unavailable",intent:"unknown"},{status:503,headers});
  }
 }
};}
