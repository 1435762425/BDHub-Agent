import {test} from 'node:test';import assert from 'node:assert/strict';
import {validateOperationsConsole} from '../src/server/operations-console/bridge.ts';
const market={market:'br',available:true,setting:{automaticOperationsEnabled:true,continuousSendEnabled:true,fullCatalogWeeklyEnabled:false},
 run:{runId:'workflow-1',state:'running',startedAt:10},current:{stage:'oecid',state:'queued',since:null,heartbeatAt:null,
  waitingOn:[{resource:'platform:global',heldBy:[{market:'my',stage:'catalog',since:5}]}]},
 lastFinished:{market:'br',runId:'workflow-1',stage:'kalodata',state:'completed',startedAt:1,finishedAt:2,errorCode:null,items:4,writeEvidence:'zero'},
 needsReview:null,openHumanCases:3,lanes:{available:true,observedAt:9,continuousSend:{state:'sending',value:12,label:'今日确认触达',lastSuccessAt:8,stopReason:null},agentReply:null}};
const payload={schemaVersion:'bdhub.operations-console.v1',checkedAt:100,markets:[market,{market:'uk',available:false,error:'no such table'}],
 resources:[{resource:'platform:global',market:'my',stage:'catalog',since:5,heartbeatAt:99}],recent:[market.lastFinished],
 scheduler:{running:true,checkedAt:99},stageLabels:{oecid:'OECID 身份'},readOnly:true,platformWrites:0};
test('console keeps waits, holders, lanes and a market that could not be read',()=>{
 const value=validateOperationsConsole(payload);
 assert.equal(value.markets[0].current.waitingOn[0].heldBy[0].market,'my');
 assert.equal(value.markets[0].lanes.continuousSend.value,12);
 assert.deepEqual(value.markets[1],{market:'uk',available:false,error:'no such table'});
});
test('console refuses write claims, unknown stage states and negative counts',()=>{
 assert.throws(()=>validateOperationsConsole({...payload,platformWrites:1}),/invalid_operations_console/);
 assert.throws(()=>validateOperationsConsole({...payload,markets:[{...market,current:{...market.current,state:'done'}}]}),/invalid_operations_console/);
 assert.throws(()=>validateOperationsConsole({...payload,markets:[{...market,openHumanCases:-1}]}),/invalid_operations_console/);
});
test('human queue and unknown waits survive validation and old payloads still read',()=>{
 const withQueue={...market,humanQueue:{human:4,technical:1},current:{...market.current,waitingKnown:false,waitReason:'roles'}};
 const value=validateOperationsConsole({...payload,markets:[withQueue]});
 assert.deepEqual(value.markets[0].humanQueue,{human:4,technical:1});
 assert.equal(value.markets[0].current.waitingKnown,false);assert.equal(value.markets[0].current.waitReason,'roles');
 const old=validateOperationsConsole(payload);
 assert.equal(old.markets[0].humanQueue,null);assert.equal(old.markets[0].current.waitingKnown,true);
 assert.throws(()=>validateOperationsConsole({...payload,markets:[{...withQueue,humanQueue:{human:-1,technical:0}}]}),/invalid_operations_console/);
});
