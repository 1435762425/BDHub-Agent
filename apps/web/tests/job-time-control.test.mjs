import test from 'node:test';
import assert from 'node:assert/strict';
import {saveJobTime} from '../src/features/ops/job-time-control.ts';

const job=id=>({id,cadence:'daily'});
const response=value=>Response.json(value);
test('saving a BR Agent window writes only its canonical market setting',async()=>{
 const calls=[];
 const result=await saveJobTime('br',job('agent_reply'),'12:15',0,async(url,init)=>{
  calls.push({url,body:init?.body?JSON.parse(init.body):null});
  if(url.startsWith('/api/jobs'))return response({market:'br',jobs:[{id:'agent_reply',at:'12:15'}]});
  return response({agentSetting:{revision:7,updatedAt:99,enabled:false,replyStart:'15:00',replyEnd:'16:00',bufferMinutes:30}});
 });
 const writes=calls.filter(row=>row.body);
 assert.equal(writes.length,1);assert.equal(writes[0].url,'/api/template-library?market=br');
 assert.equal(writes[0].body.setting.replyStart,'12:15');assert.equal(writes[0].body.setting.replyEnd,'13:15');
 assert.equal(writes[0].body.setting.enabled,false);assert.equal(writes[0].body.expectedRevision,7);
 assert.equal('updatedAt' in writes[0].body.setting,false);assert.equal(result.state.market,'br');
 assert.ok(calls.every(row=>row.url.endsWith('market=br')));
});

test('moving the Agent start keeps the saved window length',async()=>{
 const calls=[];
 await saveJobTime('br',job('agent_reply'),'01:00',0,async(url,init)=>{
  calls.push(init?.body?JSON.parse(init.body):null);
  if(url.startsWith('/api/jobs'))return response({market:'br',jobs:[]});
  return response({agentSetting:{revision:2,updatedAt:1,enabled:true,replyStart:'00:30',replyEnd:'08:00',bufferMinutes:30}});
 });
 const write=calls.find(Boolean);
 assert.equal(write.setting.replyStart,'01:00');assert.equal(write.setting.replyEnd,'08:30');
});

test('saving a send window never writes a global schedule and reports readback failure separately',async()=>{
 const calls=[];
 const result=await saveJobTime('uk',job('continuous_send'),'17:00',0,async(url,init)=>{
  calls.push({url,body:init?.body?JSON.parse(init.body):null});
  if(url.startsWith('/api/jobs'))throw Error('read failed after commit');
  return response({control:{revision:4,window:['16:30','24:00']}});
 },()=> 'single-request');
 assert.equal(result.state,null);
 assert.deepEqual(calls.filter(row=>row.body),[{url:'/api/send?market=uk',body:{action:'save',market:'uk',requestId:'send-time-single-request',expectedRevision:4,changes:{window:['17:00','24:00']}}}]);
});

test('rejected runtime window never falls through into a schedule write',async()=>{
 const calls=[];
 await assert.rejects(saveJobTime('it',job('agent_reply'),'16:30',0,async(url,init)=>{
  calls.push({url,method:init?.method});
  if(init?.method==='POST')return new Response('',{status:409});
  return response({agentSetting:{revision:1,replyStart:'15:00',replyEnd:'16:00'}});
 }));
 assert.equal(calls.length,2);assert.equal(calls.some(row=>row.url.startsWith('/api/jobs')),false);
});
