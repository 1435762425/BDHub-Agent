import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {isLocalRequest} from "../runtime/validation.ts";
import {REPLY_ACTIONS} from "../../features/second-outreach/reply-review-contracts.ts";
import type {ReplyAction,ReplyDecision,ReplyReviewState,ReplyReviewStatus} from "../../features/second-outreach/reply-review-contracts.ts";

export {REPLY_ACTIONS};
export type {ReplyAction,ReplyDecision,ReplyReviewState,ReplyReviewStatus};

type Command={action:"status";limit:number}|{action:"classify";turnId:string;requestId:string;provider:"deepseek"|"jev"}|
 {action:"review";classificationId:string;expectedRevision:number;verdict:"correct"|"incorrect";
  correctAction:ReplyAction|null;note:string};
const TOKEN=/^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$/;
const TURN=/^turn-[a-f0-9]{24}$/;
const CLASSIFICATION=/^classification-[a-f0-9]{24}$/;
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
  const comparisons=row.comparisons.map(rawComparison=>{const c=object(rawComparison),action=c.action;if(typeof action!=="string"||!REPLY_ACTIONS.includes(action as ReplyAction))throw Error("invalid_reply_review");const confidence=c.confidence;if(typeof confidence!=="number"||!Number.isFinite(confidence)||confidence<0||confidence>1)throw Error("invalid_reply_review");return {classificationId:string(c.classificationId,48),provider:string(c.provider,32),model:string(c.model,64),action:action as ReplyAction,confidence,intentCode:string(c.intentCode,48)};});
  let review:ReplyReviewState|null=null;if(row.review!=null){const r=object(row.review);if(r.verdict!=="correct"&&r.verdict!=="incorrect")throw Error("invalid_reply_review");const correct=r.correct_action;if(correct!=null&&(typeof correct!=="string"||!REPLY_ACTIONS.includes(correct as ReplyAction)))throw Error("invalid_reply_review");review={classification_id:string(r.classification_id,48),revision:integer(r.revision),verdict:r.verdict,correct_action:correct as ReplyAction|null,note:typeof r.note==="string"?r.note:"",created_at:Number(r.created_at)};}
  return {turnId,messageId:string(row.messageId,32),creatorId:string(row.creatorId,100),format:string(row.format,32),text:nullable(row.text,4000),historical:row.historical===true,occurredMs:row.occurredMs==null?null:integer(row.occurredMs),episodes,classificationId:row.classificationId==null?null:string(row.classificationId,48),decision:decision(row.decision),review,comparisons};});
 const providers=object(v.providers),deepseek=object(providers.deepseek),jev=object(providers.jev);
 return {schema:"bdhub.reply-review.v1",policyVersion:string(v.policyVersion,64),processingIntervalSeconds:integer(v.processingIntervalSeconds,86400),automaticReplies:false,providers:{deepseek:{mode:string(deepseek.mode,32)},jev:{mode:string(jev.mode,32)}},counts,items};
}

export function invokeReplyReview(input:Command):Promise<unknown>{const root=projectRoot();return new Promise((resolve,reject)=>{const child=execFile(join(root,"../01-BDSystem-V2/.venv/bin/python"),[join(root,"scripts/reply-review.py")],{cwd:root,timeout:90000,maxBuffer:4*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{try{const value=JSON.parse(stdout);if(error||value.error)throw Error(String(value.error||"reply_review_unavailable"));resolve(input.action==="status"?validateReplyReviewStatus(value):value);}catch(error){reject(error);}});child.stdin?.end(JSON.stringify(input));});}

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
export function createReplyReviewHandlers(invoke=invokeReplyReview){return {
 GET:async(request:Request)=>{if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});if(new URL(request.url).search)return Response.json({error:"invalid_query"},{status:400,headers});try{return Response.json(await invoke({action:"status",limit:12}),{headers});}catch{return Response.json({error:"reply_review_unavailable"},{status:503,headers});}},
 POST:async(request:Request)=>{if(!isLocalRequest(request,true))return Response.json({error:"local_origin_required"},{status:403,headers});if(!request.headers.get("content-type")?.startsWith("application/json"))return Response.json({error:"json_required"},{status:415,headers});let body:Record<string,unknown>;try{body=object(await request.json());}catch{return Response.json({error:"invalid_input"},{status:400,headers});}
  let command:Command;try{if(body.action==="classify"){exact(body,["action","turnId","requestId","provider"]);const turnId=string(body.turnId,40),requestId=string(body.requestId,120);if(!TURN.test(turnId)||!TOKEN.test(requestId)||(body.provider!=="deepseek"&&body.provider!=="jev"))throw Error();command={action:"classify",turnId,requestId,provider:body.provider};}else if(body.action==="review"){exact(body,["action","classificationId","expectedRevision","verdict","correctAction","note"]);const classificationId=string(body.classificationId,48);if(!CLASSIFICATION.test(classificationId)||(body.verdict!=="correct"&&body.verdict!=="incorrect"))throw Error();const correct=body.correctAction;if(correct!=null&&(typeof correct!=="string"||!REPLY_ACTIONS.includes(correct as ReplyAction)))throw Error();if(body.verdict==="correct"&&correct!=null||body.verdict==="incorrect"&&correct==null)throw Error();command={action:"review",classificationId,expectedRevision:integer(body.expectedRevision),verdict:body.verdict,correctAction:correct as ReplyAction|null,note:typeof body.note==="string"&&body.note.length<=2000?body.note:(()=>{throw Error();})()};}else throw Error();}catch{return Response.json({error:"invalid_input"},{status:400,headers});}
  try{return Response.json(await invoke(command),{headers});}catch(error){const code=error instanceof Error?error.message:"reply_review_unavailable";return Response.json({error:code},{status:code.includes("conflict")?409:code==="jev_not_configured"?422:503,headers});}}
};}
