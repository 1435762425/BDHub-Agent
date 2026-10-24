import {test} from 'node:test';import assert from 'node:assert/strict';
import {validateOperationsHome} from '../src/server/operations-home/bridge.ts';
const stage=(id,label)=>({id,label,state:'completed',counts:{handled:2},processed:2,lastSuccessAt:1,nextAt:null,checkpoint:{},stopReason:null,platformWrites:0,generationId:'generation-'+id});
const payload={schemaVersion:'bdhub.operations-home.v1',market:'it',setting:{market:'it',automaticOperationsEnabled:false,fullCatalogWeeklyEnabled:false,continuousSendEnabled:false,revision:0,updatedAt:0},workflow:{runId:null,state:'idle',startedAt:null,finishedAt:null},stages:[stage('catalog','货盘'),stage('taplink_prepare','TapLink'),stage('kalodata','Kalodata'),stage('oecid','OECID'),stage('send_pool','发送池'),stage('continuous_send','持续二发'),stage('agent_reply','Agent 回复')],issues:[],jobs:{},accounts:{},continuousSend:{},agent:{enabled:false,revision:0,replyWindow:['15:00','16:00']},templateReview:{minimumApproved:10,approved:0,total:12,ready:false},readOnly:true,platformWrites:0,realSends:0};
test('operations home accepts one reconciled seven-stage chain',()=>{const value=validateOperationsHome(payload);assert.equal(value.stages.length,7);assert.equal(value.setting.automaticOperationsEnabled,false);assert.equal(value.stages[0].generationId,'generation-catalog');});
test('home refuses demo writes, missing stages and malformed issues',()=>{assert.throws(()=>validateOperationsHome({...payload,platformWrites:1}),/invalid_operations_home/);assert.throws(()=>validateOperationsHome({...payload,stages:payload.stages.slice(1)}),/invalid_operations_home/);assert.throws(()=>validateOperationsHome({...payload,issues:[{kind:'stage',id:'x',title:'x',reason:'x'.repeat(241)}]}),/invalid_operations_home/);});
test('stage figures survive validation and malformed ones are refused',()=>{
 const stages=payload.stages.map((stage,index)=>index===0?{...stage,metric:{label:'本轮货盘商品',unit:'item',scope:'run',value:null,availability:'not_recorded'},writes:{value:null,availability:'uncertain'}}:stage);
 const value=validateOperationsHome({...payload,stages});
 assert.deepEqual(value.stages[0].metric,{label:'本轮货盘商品',unit:'item',scope:'run',value:null,availability:'not_recorded'});
 assert.equal(value.stages[0].writes.availability,'uncertain');
 assert.equal(value.stages[1].metric,undefined);
 assert.throws(()=>validateOperationsHome({...payload,stages:stages.map((s,i)=>i===0?{...s,metric:{...s.metric,scope:'forever'}}:s)}),/invalid_operations_home/);
});

const runtime={state:'outside_reply_window',reportedState:'outside_reply_window',observedAt:100,fresh:true,running:true,enabled:true,statusReason:null,statusSource:'worker',workerObservedAt:100,controlUpdatedAt:10,value:null,label:null,unit:null,scope:null,lastSuccessAt:null,stopReason:null};
const lastRun={runId:'reply-run-failed',state:'failed',startedAt:40,finishedAt:50,errorCode:'model_unavailable'};
const withAgent=changes=>({...payload,stages:payload.stages.map(row=>row.id==='agent_reply'?{...row,...changes}:row)});
test('home retains current window wait and a separate failed execution history',()=>{
 const row=validateOperationsHome(withAgent({state:runtime.state,runtime,lastRun})).stages.at(-1);
 assert.equal(row.state,'outside_reply_window');assert.equal(row.runtime.fresh,true);
 assert.deepEqual(row.lastRun,lastRun);
 const missing=validateOperationsHome(withAgent({state:'unverified',runtime:{...runtime,state:'unverified',fresh:false,statusReason:'status_stale'},lastRun:null})).stages.at(-1);
 assert.equal(missing.state,'unverified');assert.equal(missing.runtime.statusReason,'status_stale');assert.equal(missing.lastRun,null);
});
test('home reuses worker freshness and control proofs instead of accepting contradictory healthy states',()=>{
 for(const changes of [
  {state:'outside_reply_window',runtime:{...runtime,fresh:false}},
  {state:'outside_reply_window',runtime:{...runtime,running:false}},
  {state:'outside_reply_window',runtime:{...runtime,observedAt:null}},
  {state:'outside_reply_window',runtime,lastRun:{...lastRun,finishedAt:-1}},
 ])assert.throws(()=>validateOperationsHome(withAgent(changes)),/invalid_operations_/);
 const off={...runtime,state:'disabled',reportedState:'failed',fresh:true,running:false,enabled:false,statusSource:'control'};
 assert.equal(validateOperationsHome(withAgent({state:'disabled',runtime:off,lastRun})).stages.at(-1).state,'disabled');
});
