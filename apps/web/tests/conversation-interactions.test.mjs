import test from 'node:test';
import assert from 'node:assert/strict';
import {ConversationController} from '../src/features/conversations/conversation-controller.ts';

const flush=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((ok,no)=>{resolve=ok;reject=no;});return {promise,resolve,reject};};
const response=value=>Response.json(value);
const detail=(cid,extra={})=>({available:true,conversationId:cid,latestTurnId:'turn-1',
 creator:{creatorId:`creator-${cid}`,revision:1,collaboration:{status:'normal',revision:0}},
 draft:{text:`draft-${cid}`,revision:0},pendingManualReplies:[],episodes:[],...extra});
const queue=(items,total=items.length,extra={})=>({view:'human',query:'',offset:0,limit:30,nextOffset:null,total,
 items:items.map(cid=>({creatorId:`creator-${cid}`,conversationId:cid})),...extra});
const body=init=>JSON.parse(init.body);

test('rapid selection clears the previous editor and ignores an out-of-order detail response',async()=>{
 const reads=new Map([['1',deferred()],['2',deferred()]]),writes=[];
 const controller=new ConversationController('it',async(url,init)=>{
  if(init?.method==='POST'){writes.push(body(init));return response({state:'unknown',requestRef:writes.at(-1).requestId,replyId:'reply-2'});}
  return reads.get(new URL(url,'http://local').searchParams.get('cid')).promise;
 },()=> 'original-request');
 controller.select('1');controller.select('2');
 assert.equal(controller.getSnapshot().detail,null);
 await controller.sendText();assert.equal(writes.length,0);
 reads.get('2').resolve(response(detail('2')));await flush();
 reads.get('1').resolve(response(detail('1')));await flush();
 assert.equal(controller.getSnapshot().detail.conversationId,'2');
 await controller.sendText();assert.equal(writes[0].cid,'2');
});

test('translation and mutation completion cannot overwrite a newly selected conversation',async()=>{
 const translation=deferred();
 const controller=new ConversationController('it',async(url,init)=>init?.method==='POST'?translation.promise:
  response(detail(new URL(url,'http://local').searchParams.get('cid'))));
 controller.select('1');await flush();
 const translating=controller.translate();controller.select('2');await flush();
 translation.resolve(response({translation:'late result for creator 1'}));await translating;
 assert.equal(controller.getSnapshot().detail.conversationId,'2');
 assert.equal(controller.getSnapshot().draft,'draft-2');
});

test('queue refresh removes resolved people and clears an empty selected queue',async()=>{
 let list=queue(['1']);
 const controller=new ConversationController('it',async url=>new URL(url,'http://local').searchParams.has('cid')?
  response(detail('1')):response(list));
 await controller.loadList();await flush();assert.equal(controller.getSnapshot().list.items.length,1);
 list=queue([]);await controller.loadList();
 assert.equal(controller.getSnapshot().list.total,0);
 assert.deepEqual(controller.getSnapshot().list.items,[]);
 assert.equal(controller.getSnapshot().selected,null);
 assert.equal(controller.getSnapshot().detail,null);
});

test('late filtered results and old pagination cannot repopulate a refreshed queue',async()=>{
 const page=deferred(),oldFilter=deferred();let initial=true;
 const controller=new ConversationController('it',async url=>{
  const params=new URL(url,'http://local').searchParams;
  if(params.has('cid'))return response(detail(params.get('cid')));
  if(params.has('offset'))return page.promise;
  if(params.get('query')==='old')return oldFilter.promise;
  if(params.get('query')==='new')return response(queue([],0,{query:'new'}));
  if(initial){initial=false;return response(queue(['1'],31,{nextOffset:30}));}
  return response(queue([]));
 });
 await controller.loadList();const loadingPage=controller.loadMore();await controller.loadList();
 page.resolve(response(queue(['2'],31,{offset:30})));await loadingPage;
 assert.deepEqual(controller.getSnapshot().list.items,[]);
 controller.setFilter('human','old');controller.setFilter('human','new');await flush();
 oldFilter.resolve(response(queue(['3'],1,{query:'old'})));await flush();
 assert.equal(controller.getSnapshot().list.query,'new');assert.equal(controller.getSnapshot().selected,null);
});

test('unknown manual send preserves the draft and reconciles exactly one original request',async()=>{
 const calls=[];let reconciliations=0;
 const controller=new ConversationController('it',async(url,init)=>{
  if(init?.method!=='POST')return response(detail('1'));
  const input=body(init);calls.push(input);
  const state=input.action==='reconcile_manual'&&++reconciliations===2?'confirmed':'unknown';
  return response({state,replyId:'manual-reply-1',requestRef:input.requestId,platformWrites:0,realSends:0});
 },()=> 'same-original');
 controller.select('1');await flush();controller.setDraft('frozen original text');
 await controller.sendText();
 assert.equal(controller.getSnapshot().draft,'frozen original text');
 assert.equal(controller.getSnapshot().pendingManualReplies.length,1);
 await controller.sendText();assert.equal(calls.length,1);
 await controller.reconcileManual('manual-same-original');
 await controller.reconcileManual('manual-same-original');
 assert.deepEqual(calls.map(row=>row.action),['send_text','reconcile_manual','reconcile_manual']);
 assert.deepEqual(calls.slice(1),Array(2).fill({action:'reconcile_manual',cid:'1',requestId:'manual-same-original',market:'it'}));
 assert.equal(controller.getSnapshot().pendingManualReplies.length,0);
 // Confirming an earlier send does not clear the editor; the operator decides what to do with it.
 assert.equal(controller.getSnapshot().draft,'frozen original text');
});

test('confirming an earlier unknown send keeps the new draft written afterwards',async()=>{
 const controller=new ConversationController('it',async(url,init)=>{
  if(init?.method!=='POST')return response(detail('1'));
  const input=body(init);
  return response({state:input.action==='reconcile_manual'?'confirmed':'unknown',replyId:'manual-reply-1',requestRef:input.requestId,platformWrites:0,realSends:0});
 },()=> 'original-a');
 controller.select('1');await flush();controller.setDraft('A: original reply');await controller.sendText();
 controller.setDraft('B: later unsent draft');
 await controller.reconcileManual('manual-original-a');
 assert.equal(controller.getSnapshot().pendingManualReplies.length,0);
 assert.equal(controller.getSnapshot().draft,'B: later unsent draft');
});

test('page reload recovers durable pending manual intent without creating another send',async()=>{
 const calls=[],pending={id:'manual-card-1',kind:'manual_card',requestId:'persisted-request-1',state:'accepted'};
 const controller=new ConversationController('it',async(url,init)=>{
  if(init?.method!=='POST')return response(detail('1',{pendingManualReplies:[pending]}));
  calls.push(body(init));return response({state:'unknown',requestRef:pending.requestId,replyId:pending.id});
 });
 controller.select('1');await flush();await controller.sendCard('episode-1');assert.equal(calls.length,0);
 await controller.reconcileManual(pending.requestId);
 assert.deepEqual(calls,[{action:'reconcile_manual',cid:'1',requestId:pending.requestId,market:'it'}]);
 assert.equal(controller.getSnapshot().pendingManualReplies[0].state,'unknown');
});

test('lost HTTP reply retains its original ID and a ready reconciliation never sends',async()=>{
 const calls=[];
 const controller=new ConversationController('it',async(url,init)=>{
  if(init?.method!=='POST')return response(detail('1'));
  const input=body(init);calls.push(input);
  if(input.action==='send_text')throw Error('response lost');
  return response({state:'ready',requestRef:input.requestId,replyId:'manual-reply-ready'});
 },()=> 'lost-response');
 controller.select('1');await flush();await controller.sendText();
 const original=controller.getSnapshot().pendingManualReplies[0].requestId;
 await controller.reconcileManual(original);await controller.sendText();
 assert.deepEqual(calls.map(row=>row.action),['send_text','reconcile_manual']);
 assert.equal(calls[1].requestId,calls[0].requestId);
 assert.equal(controller.getSnapshot().pendingManualReplies[0].state,'ready');
});

test('confirmed send after a selection change does not restore the old conversation',async()=>{
 const sent=deferred();let requestId;
 const controller=new ConversationController('it',async(url,init)=>{
  if(init?.method==='POST'){requestId=body(init).requestId;return sent.promise;}
  return response(detail(new URL(url,'http://local').searchParams.get('cid')));
 });
 controller.select('1');await flush();const sending=controller.sendText();
 controller.select('2');await flush();sent.resolve(response({state:'confirmed',requestRef:requestId,replyId:'reply-1'}));
 await sending;
 assert.equal(controller.getSnapshot().detail.conversationId,'2');assert.equal(controller.getSnapshot().draft,'draft-2');
});

test('unmounted conversation ignores outstanding reads',async()=>{
 const pending=deferred(),controller=new ConversationController('it',()=>pending.promise);
 controller.select('1');controller.dispose();pending.resolve(response(detail('1')));await flush();
 assert.equal(controller.getSnapshot().detail,null);
});

test('default fetcher keeps the browser receiver so the queue loads without an injected fetcher',async()=>{
 const original=globalThis.fetch,calls=[];
 globalThis.fetch=function(url){
  // Browsers throw "Illegal invocation" unless fetch runs on window or with no receiver at all.
  if(this!==undefined&&this!==globalThis)throw new TypeError("Failed to execute 'fetch' on 'Window': Illegal invocation");
  calls.push(String(url));
  return Promise.resolve(response(queue([],0,{counts:{human:0,technical:0,agent:48,waiting:0,completed:0,all:48}})));
 };
 try{
  const controller=new ConversationController('br');
  await controller.loadList();
  assert.equal(controller.getSnapshot().error,'');
  assert.equal(controller.getSnapshot().list.counts.all,48);
  assert.equal(calls[0],'/api/conversations?market=br&view=human&query=');
 }finally{globalThis.fetch=original;}
});

test('unsaved drafts survive switching conversations and every server re-read',async()=>{
 let serverRevision=0;
 const controller=new ConversationController('it',async url=>{
  const cid=new URL(url,'http://local').searchParams.get('cid');
  return response(detail(cid,{draft:{text:`server-${cid}-${serverRevision}`,revision:serverRevision}}));
 });
 controller.select('1');await flush();assert.equal(controller.getSnapshot().draft,'server-1-0');
 controller.setDraft('typed for creator 1');
 controller.select('2');await flush();assert.equal(controller.getSnapshot().draft,'server-2-0');
 controller.select('1');await flush();
 assert.equal(controller.getSnapshot().draft,'typed for creator 1');
 serverRevision=1;await controller.refresh();
 assert.equal(controller.getSnapshot().draft,'typed for creator 1');
 assert.match(controller.getSnapshot().draftNotice,/另一个窗口/);
});

test('periodic refresh updates the open timeline without resetting translations or the draft',async()=>{
 let timeline=[{id:'turn-1',direction:'inbound',kind:'text',text:'ciao',occurredAt:1,status:'received',source:'creator'}];
 const controller=new ConversationController('it',async url=>{
  const params=new URL(url,'http://local').searchParams;
  return params.has('cid')?response(detail('1',{timeline:[...timeline]})):response(queue(['1']));
 });
 controller.select('1');await flush();controller.setDraft('half written');
 timeline=[...timeline,{id:'turn-2',direction:'inbound',kind:'text',text:'nuova domanda',occurredAt:2,status:'received',source:'creator'}];
 await controller.refresh();await flush();
 assert.deepEqual(controller.getSnapshot().detail.timeline.map(row=>row.id),['turn-1','turn-2']);
 assert.equal(controller.getSnapshot().draft,'half written');
});

test('a definite refusal clears the local intent but a lost or unknown reply keeps it',async()=>{
 let mode='refused';const calls=[];
 const controller=new ConversationController('it',async(url,init)=>{
  if(init?.method!=='POST')return response(detail('1'));
  calls.push(body(init));
  if(mode==='refused')return Response.json({error:'input_too_large',intent:'absent'},{status:409});
  return Response.json({error:'conversation_workbench_unavailable',intent:'unknown'},{status:503});
 },()=>`id-${calls.length}`);
 controller.select('1');await flush();controller.setDraft('中'.repeat(3500));
 await controller.sendText();
 assert.equal(controller.getSnapshot().pendingManualReplies.length,0);
 assert.equal(controller.getSnapshot().draft,'中'.repeat(3500));
 assert.match(controller.getSnapshot().error,/未发送/);
 mode='unknown';await controller.sendText();
 assert.equal(controller.getSnapshot().pendingManualReplies.length,1);
 assert.equal(calls.length,2);
});

test('an intent not found yet keeps the gate; only a closed command releases the editor',async()=>{
 let outcome='unresolved';
 const controller=new ConversationController('it',async(url,init)=>{
  if(init?.method!=='POST')return response(detail('1'));
  const input=body(init);
  if(input.action==='send_text')throw Error('response lost');
  return Response.json({error:'manual_reconcile_intent_missing',intent:outcome},{status:409});
 },()=> 'never-created');
 controller.select('1');await flush();controller.setDraft('text');await controller.sendText();
 for(const pending of ['unresolved','absent','unknown']){
  outcome=pending;await controller.reconcileManual('manual-never-created');
  assert.equal(controller.getSnapshot().pendingManualReplies.length,1,pending);
 }
 outcome='not_submitted';await controller.reconcileManual('manual-never-created');
 assert.equal(controller.getSnapshot().pendingManualReplies.length,0);
 assert.equal(controller.getSnapshot().draft,'text');
});

test('older history is paged in above the newest page without duplicates',async()=>{
 const newest=[{id:'b',direction:'inbound',kind:'text',text:'2',occurredAt:2,status:'received',source:'creator'}];
 const older=[{id:'a',direction:'inbound',kind:'text',text:'1',occurredAt:1,status:'received',source:'creator'},newest[0]];
 const requested=[];
 const controller=new ConversationController('it',async url=>{
  const params=new URL(url,'http://local').searchParams;requested.push(params.get('before'));
  return params.get('before')?response(detail('1',{timeline:older,timelineCursor:null,timelineHasOlder:false})):
   response(detail('1',{timeline:newest,timelineCursor:'2|b',timelineHasOlder:true}));
 });
 controller.select('1');await flush();await controller.loadOlder();
 assert.deepEqual(requested,[null,'2|b']);
 assert.deepEqual(controller.getSnapshot().olderTimeline.map(row=>row.id),['a']);
 assert.equal(controller.getSnapshot().olderCursor,null);
});
