import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createTaskHandlers,TaskInputError} from '../src/server/batch-tasks/bridge.ts';
const request=(body,origin='http://127.0.0.1:5198')=>new Request('http://127.0.0.1:5198/api/batch-tasks',{method:'POST',headers:{Host:'127.0.0.1:5198',Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
test('preview and listing never wake worker; confirm/resume wake only after durable success',async()=>{
 let wakes=0,calls=[];const {GET,POST}=createTaskHandlers(async b=>{calls.push(b);return {ok:true}},()=>wakes++);
 await GET(new Request('http://127.0.0.1:5198/api/batch-tasks',{headers:{Host:'127.0.0.1:5198'}}));
 assert.equal((await POST(request({action:'preview',spec:{target:3000}}))).status,200);assert.equal(wakes,0);
 await POST(request({action:'confirm',token:'abc',requestKey:'key',authorizationScope:'full_preparation_no_messages'}));assert.equal(wakes,1);
 await POST(request({action:'resume',id:'batch-1',revision:2}));assert.equal(wakes,2);assert.equal(calls.length,4);
});
test('arbitrary commands, invalid revisions and external requests cannot touch store',async()=>{
 let calls=0;const {POST}=createTaskHandlers(async()=>{calls++;return{}},()=>{});
 assert.equal((await POST(request({action:'list'},'https://external.invalid'))).status,403);
 for(const b of [{action:'send'},{action:'confirm',token:'abc',requestKey:'old-ui'},{action:'confirm',token:'abc',requestKey:'key',authorizationScope:'full_preparation_no_messages',send:true},{action:'resume',id:'one',revision:true},{action:'priority',id:'one',revision:1,priority:101}])assert.equal((await POST(request(b))).status,400);
 assert.equal(calls,0);
});
test('confirmation failure does not launch preparation',async()=>{
 let wakes=0;const {POST}=createTaskHandlers(async()=>{throw new TaskInputError('task_card_stale')},()=>wakes++);
 const r=await POST(request({action:'confirm',token:'abc',requestKey:'key',authorizationScope:'full_preparation_no_messages'}));assert.equal(r.status,400);assert.equal((await r.json()).error,'task_card_stale');assert.equal(wakes,0);
});
