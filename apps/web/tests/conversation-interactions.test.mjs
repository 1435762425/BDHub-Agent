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
 assert.equal(controller.getSnapshot().draft,'');
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
