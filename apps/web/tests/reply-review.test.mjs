import test from 'node:test';
import assert from 'node:assert/strict';
import {createReplyReviewHandlers,validateReplyReviewStatus} from '../src/server/reply-review/bridge.ts';

const url='http://127.0.0.1:5198/api/reply-review';
const headers={host:'127.0.0.1:5198',origin:'http://127.0.0.1:5198','content-type':'application/json'};
const turn='turn-'+'a'.repeat(24),classification='classification-'+'b'.repeat(24);
const decision={action:'collaboration_ack',intentCode:'collaboration_confirmed',evidenceMessageIds:['1001'],
 evidenceQuotes:['farò un video'],confidence:.98,humanReason:null,templateKey:'collaboration_ack_v1',
 meaningZh:'达人确认会制作视频',relatedEpisodeIds:['episode-'+'c'.repeat(24)],templateText:'Perfetto!',
 provider:'deepseek',model:'deepseek-flash',policyVersion:'creator-reply-actions-v1',automaticReply:false,executionAllowed:false};
const payload={schema:'bdhub.reply-review.v1',policyVersion:'creator-reply-actions-v1',processingIntervalSeconds:7200,
 automaticReplies:false,providers:{deepseek:{mode:'shadow'},jev:{mode:'unconfigured'}},
 counts:{turns:35,episodes:10,linkedTurns:20,classified:1,reviewed:0},evaluation:{paired:1,agreements:1,disagreements:0,agreementRate:1,reviewedTurns:0,pendingReview:35,providers:{deepseek:{evaluated:0,correct:0,accuracy:null,falseAuto:0,falseHuman:0},jev:{evaluated:0,correct:0,accuracy:null,falseAuto:0,falseHuman:0}},disagreementSamples:[]},items:[{turnId:turn,messageId:'1001',
 creatorId:'creator-1',format:'text',text:'Certo, farò un video',historical:true,occurredMs:1789257600000,
 episodes:[{episode_id:'episode-'+'c'.repeat(24),pid:'1729571380453480001',list_id:'8650765182615984910',candidate_rank:1,confidence:'high'}],
 classificationId:classification,decision,review:null,comparisons:[{classificationId:classification,provider:'deepseek',model:'deepseek-flash',action:'collaboration_ack',confidence:.98,intentCode:'collaboration_confirmed',decision}]}]};

test('reply review decoder keeps event links and refuses executable classifications',()=>{
 const value=validateReplyReviewStatus(payload);assert.equal(value.items[0].decision.action,'collaboration_ack');
 const bad=structuredClone(payload);bad.items[0].decision.executionAllowed=true;
 assert.throws(()=>validateReplyReviewStatus(bad),/invalid_reply_review/);
});

test('GET is local read-only and POST accepts only classify or versioned review',async()=>{
 const calls=[];const handlers=createReplyReviewHandlers(async command=>{calls.push(command);return command.action==='status'?payload:{ok:true};});
 assert.equal((await handlers.GET(new Request(url,{headers:{host:'127.0.0.1:5198'}}))).status,200);
 assert.deepEqual(calls[0],{action:'status',limit:12});
 const classify={action:'classify',turnId:turn,requestId:'web-request-0001',provider:'deepseek'};
 assert.equal((await handlers.POST(new Request(url,{method:'POST',headers,body:JSON.stringify(classify)}))).status,200);
 const review={action:'review_turn',turnId:turn,expectedRevision:0,correctAction:'human',note:'应转人工'};
 assert.equal((await handlers.POST(new Request(url,{method:'POST',headers,body:JSON.stringify(review)}))).status,200);
 assert.equal((await handlers.POST(new Request(url,{method:'POST',headers,body:JSON.stringify({...classify,send:true})}))).status,400);
 assert.equal((await handlers.POST(new Request(url,{method:'POST',headers,body:JSON.stringify({...review,action:'send'})}))).status,400);
});

test('foreign origin and malformed review do not invoke the backend',async()=>{
 let calls=0;const handlers=createReplyReviewHandlers(async()=>{calls++;return {};});
 const review={action:'review_turn',turnId:turn,expectedRevision:0,correctAction:null,note:''};
 assert.equal((await handlers.POST(new Request(url,{method:'POST',headers:{...headers,origin:'https://other.test'},body:JSON.stringify(review)}))).status,403);
 assert.equal((await handlers.POST(new Request(url,{method:'POST',headers,body:JSON.stringify(review)}))).status,400);
 assert.equal(calls,0);
});
