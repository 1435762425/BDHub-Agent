import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {isLocalRequest} from "../runtime/validation.ts";
import {REPLY_ACTIONS} from "../../features/second-outreach/reply-review-contracts.ts";
import type {ReplyAction,ReplyDecision,ReplyReviewItem,ReplyReviewState,ReplyReviewStatus} from "../../features/second-outreach/reply-review-contracts.ts";

export {REPLY_ACTIONS};
export type {ReplyAction,ReplyDecision,ReplyReviewState,ReplyReviewStatus};

type Command={action:"status";limit:number}|{action:"classify";turnId:string;requestId:string;provider:"deepseek"|"jev"}|
 {action:"review_turn";turnId:string;expectedRevision:number;correctAction:ReplyAction;note:string}|
 {action:"apply_review";turnId:string;expectedReviewRevision:number;expectedControlRevision:number;
  expectedPendingRevision:number;requestId:string};
const TOKEN=/^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$/;
const TURN=/^turn-[a-f0-9]{24}$/;
const integer=(value:unknown,max=Number.MAX_SAFE_INTEGER)=>{if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0||value>max)throw Error("invalid_reply_review");return value;};
const string=(value:unknown,max=500)=>{if(typeof value!=="string"||!value||value.length>max)throw Error("invalid_reply_review");return value;};
const nullable=(value:unknown,max=500)=>value==null?null:string(value,max);
const object=(value:unknown)=>{if(!value||typeof value!=="object"||Array.isArray(value))throw Error("invalid_reply_review");return value as Record<string,unknown>;};
const exact=(value:Record<string,unknown>,keys:string[])=>{if(Object.keys(value).sort().join(",")!==[...keys].sort().join(","))throw Error("invalid_reply_review");};

function decision(value:unknown):ReplyDecision|null{
 if(value==null)return null;const v=object(value);
 const action=v.action;if(typeof action!=="string"||!REPLY_ACTIONS.includes(action as ReplyAction))throw Error("invalid_reply_review");
 const ids=v.evidenceMessageIds,quotes=v.evidenceQuotes,episodes=v.relatedEpisodeIds;
 if(!Array.isArray(ids)||!Array.isArray(quotes)||!Array.isArray(episodes)||ids.length===0||ids.length>3||quotes.length===0||quotes.length>5||episodes.length>3)throw Error("invalid_reply_review");
 const confidence=v.confidence;if(typeof confidence!=="number"||!Number.isFinite(confidence)||confidence<0||confidence>1)throw Error("invalid_reply_review");
 if(v.automaticReply!==false||v.executionAllowed!==false)throw Error("invalid_reply_review");
 return {action:action as ReplyAction,intentCode:string(v.intentCode,48),
  evidenceMessageIds:ids.map(x=>string(x,32)),evidenceQuotes:quotes.map(x=>string(x,500)),confidence,
  humanReason:nullable(v.humanReason,200),templateKey:nullable(v.templateKey,64),meaningZh:string(v.meaningZh,500),
  relatedEpisodeIds:episodes.map(x=>string(x,40)),templateText:nullable(v.templateText,1200),
  provider:string(v.provider,32),model:string(v.model,64),policyVersion:string(v.policyVersion,64),
  automaticReply:false,executionAllowed:false};
}

export function validateReplyReviewStatus(value:unknown):ReplyReviewStatus{
 const v=object(value);if(v.schema!=="bdhub.reply-review.v1"||v.automaticReplies!==false)throw Error("invalid_reply_review");
 const countsRaw=object(v.counts);exact(countsRaw,["turns","episodes","linkedTurns","classified","reviewed"]);
 const counts={turns:integer(countsRaw.turns),episodes:integer(countsRaw.episodes),linkedTurns:integer(countsRaw.linkedTurns),classified:integer(countsRaw.classified),reviewed:integer(countsRaw.reviewed)};
 if(!Array.isArray(v.items)||v.items.length>50)throw Error("invalid_reply_review");
 const items=v.items.map(raw=>{const row=object(raw);const turnId=string(row.turnId,40);if(!TURN.test(turnId))throw Error("invalid_reply_review");
  if(!Array.isArray(row.episodes)||row.episodes.length>3)throw Error("invalid_reply_review");
  const episodes=row.episodes.map(rawEpisode=>{const e=object(rawEpisode);return {episode_id:string(e.episode_id,40),pid:string(e.pid,32),list_id:string(e.list_id,32),candidate_rank:integer(e.candidate_rank,3),confidence:string(e.confidence,16)};});
  if(!Array.isArray(row.comparisons)||row.comparisons.length>3)throw Error("invalid_reply_review");
  const comparisons=row.comparisons.map(rawComparison=>{const c=object(rawComparison),action=c.action;if(typeof action!=="string"||!REPLY_ACTIONS.includes(action as ReplyAction))throw Error("invalid_reply_review");const confidence=c.confidence;if(typeof confidence!=="number"||!Number.isFinite(confidence)||confidence<0||confidence>1)throw Error("invalid_reply_review");const parsed=decision(c.decision);if(!parsed||parsed.action!==action)throw Error("invalid_reply_review");return {classificationId:string(c.classificationId,48),provider:string(c.provider,32),model:string(c.model,64),action:action as ReplyAction,confidence,intentCode:string(c.intentCode,48),decision:parsed};});
  let review:ReplyReviewState|null=null;if(row.review!=null){const r=object(row.review),correct=r.correct_action;if(typeof correct!=="string"||!REPLY_ACTIONS.includes(correct as ReplyAction))throw Error("invalid_reply_review");review={turn_id:string(r.turn_id,40),revision:integer(r.revision),correct_action:correct as ReplyAction,note:typeof r.note==="string"?r.note:"",created_at:Number(r.created_at)};}
  const operationalRaw=object(row.operational);let application:null|Record<string,unknown>=null;if(operationalRaw.application!=null){const a=object(operationalRaw.application),action=a.action;if(typeof action!=="string"||!REPLY_ACTIONS.includes(action as ReplyAction)||a.automaticReply!==false||integer(a.platformWrites)!==0)throw Error("invalid_reply_review");application={turnId:string(a.turnId,40),action:action as ReplyAction,reviewRevision:integer(a.reviewRevision),state:string(a.state,48),automaticReply:false,platformWrites:0};for(const key of ["caseId","candidateId","templateKey"]){if(a[key]!=null)application[key]=string(a[key],80);}}
  if(typeof operationalRaw.applicable!=="boolean")throw Error("invalid_reply_review");const operational={applicable:operationalRaw.applicable,reason:nullable(operationalRaw.reason,80),controlRevision:operationalRaw.controlRevision==null?null:integer(operationalRaw.controlRevision),pendingRevision:operationalRaw.pendingRevision==null?null:integer(operationalRaw.pendingRevision),pendingState:nullable(operationalRaw.pendingState,64),application:application as ReplyReviewItem["operational"]["application"]};
  return {turnId,messageId:string(row.messageId,32),creatorId:string(row.creatorId,100),format:string(row.format,32),text:nullable(row.text,4000),historical:row.historical===true,occurredMs:row.occurredMs==null?null:integer(row.occurredMs),episodes,classificationId:row.classificationId==null?null:string(row.classificationId,48),decision:decision(row.decision),review,comparisons,operational};});
 const providers=object(v.providers),deepseek=object(providers.deepseek),jev=object(providers.jev);
 const evaluationRaw=object(v.evaluation),metricRaw=object(evaluationRaw.providers);
 const metric=(raw:unknown)=>{const m=object(raw),accuracy=m.accuracy;if(accuracy!=null&&(typeof accuracy!=="number"||!Number.isFinite(accuracy)||accuracy<0||accuracy>1))throw Error("invalid_reply_review");return {evaluated:integer(m.evaluated),correct:integer(m.correct),accuracy:accuracy as number|null,falseAuto:integer(m.falseAuto),falseHuman:integer(m.falseHuman)};};
 const agreementRate=evaluationRaw.agreementRate;if(agreementRate!=null&&(typeof agreementRate!=="number"||!Number.isFinite(agreementRate)||agreementRate<0||agreementRate>1))throw Error("invalid_reply_review");
 if(!Array.isArray(evaluationRaw.disagreementSamples)||evaluationRaw.disagreementSamples.length>5)throw Error("invalid_reply_review");
 const disagreementSamples=evaluationRaw.disagreementSamples.map(raw=>{const row=object(raw),deep=row.deepseek,jevAction=row.jev;if(typeof deep!=="string"||!REPLY_ACTIONS.includes(deep as ReplyAction)||typeof jevAction!=="string"||!REPLY_ACTIONS.includes(jevAction as ReplyAction)||row.agree!==false)throw Error("invalid_reply_review");return {turnId:string(row.turnId,40),deepseek:deep as ReplyAction,jev:jevAction as ReplyAction,agree:false};});
 const evaluation={paired:integer(evaluationRaw.paired),agreements:integer(evaluationRaw.agreements),disagreements:integer(evaluationRaw.disagreements),agreementRate:agreementRate as number|null,reviewedTurns:integer(evaluationRaw.reviewedTurns),pendingReview:integer(evaluationRaw.pendingReview),providers:{deepseek:metric(metricRaw.deepseek),jev:metric(metricRaw.jev)},disagreementSamples};
 return {schema:"bdhub.reply-review.v1",policyVersion:string(v.policyVersion,64),processingIntervalSeconds:integer(v.processingIntervalSeconds,86400),automaticReplies:false,providers:{deepseek:{mode:string(deepseek.mode,32)},jev:{mode:string(jev.mode,32)}},counts,evaluation,items};
}

export function invokeReplyReview(input:Command):Promise<unknown>{const root=projectRoot();return new Promise((resolve,reject)=>{const child=execFile(join(root,"../01-BDSystem-V2/.venv/bin/python"),[join(root,"scripts/reply-review.py")],{cwd:root,timeout:90000,maxBuffer:4*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{try{const value=JSON.parse(stdout);if(error||value.error)throw Error(String(value.error||"reply_review_unavailable"));resolve(input.action==="status"?validateReplyReviewStatus(value):value);}catch(error){reject(error);}});child.stdin?.end(JSON.stringify(input));});}

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
export function createReplyReviewHandlers(invoke=invokeReplyReview){return {
 GET:async(request:Request)=>{if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});if(new URL(request.url).search)return Response.json({error:"invalid_query"},{status:400,headers});try{return Response.json(await invoke({action:"status",limit:12}),{headers});}catch{return Response.json({error:"reply_review_unavailable"},{status:503,headers});}},
 POST:async(request:Request)=>{if(!isLocalRequest(request,true))return Response.json({error:"local_origin_required"},{status:403,headers});if(!request.headers.get("content-type")?.startsWith("application/json"))return Response.json({error:"json_required"},{status:415,headers});let body:Record<string,unknown>;try{body=object(await request.json());}catch{return Response.json({error:"invalid_input"},{status:400,headers});}
  let command:Command;try{if(body.action==="classify"){exact(body,["action","turnId","requestId","provider"]);const turnId=string(body.turnId,40),requestId=string(body.requestId,120);if(!TURN.test(turnId)||!TOKEN.test(requestId)||(body.provider!=="deepseek"&&body.provider!=="jev"))throw Error();command={action:"classify",turnId,requestId,provider:body.provider};}else if(body.action==="review_turn"){exact(body,["action","turnId","expectedRevision","correctAction","note"]);const turnId=string(body.turnId,40),correct=body.correctAction;if(!TURN.test(turnId)||typeof correct!=="string"||!REPLY_ACTIONS.includes(correct as ReplyAction))throw Error();command={action:"review_turn",turnId,expectedRevision:integer(body.expectedRevision),correctAction:correct as ReplyAction,note:typeof body.note==="string"&&body.note.length<=2000?body.note:(()=>{throw Error();})()};}else if(body.action==="apply_review"){exact(body,["action","turnId","expectedReviewRevision","expectedControlRevision","expectedPendingRevision","requestId"]);const turnId=string(body.turnId,40),requestId=string(body.requestId,120);if(!TURN.test(turnId)||!TOKEN.test(requestId))throw Error();command={action:"apply_review",turnId,expectedReviewRevision:integer(body.expectedReviewRevision),expectedControlRevision:integer(body.expectedControlRevision),expectedPendingRevision:integer(body.expectedPendingRevision),requestId};}else throw Error();}catch{return Response.json({error:"invalid_input"},{status:400,headers});}
  try{return Response.json(await invoke(command),{headers});}catch(error){const code=error instanceof Error?error.message:"reply_review_unavailable";const unprocessable=new Set(["jev_not_configured","historical_turn_not_applicable","review_application_not_current","relationship_control_changed","link_episode_not_unique"]);return Response.json({error:code},{status:code.includes("conflict")||code==="turn_content_changed"?409:unprocessable.has(code)?422:503,headers});}}
};}
