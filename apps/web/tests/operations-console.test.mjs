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
test('a paused full-managed selection survives validation; absent reads as not paused',()=>{
 const paused={...market,selectionPaused:{reason:'commerce_verification_required',pending:12,pausedAt:1000,retryAt:11800}};
 const value=validateOperationsConsole({...payload,markets:[paused]});
 assert.deepEqual(value.markets[0].selectionPaused,{reason:'commerce_verification_required',pending:12,pausedAt:1000,retryAt:11800});
 assert.equal(validateOperationsConsole(payload).markets[0].selectionPaused,null);
 assert.throws(()=>validateOperationsConsole({...payload,markets:[{...paused,selectionPaused:{pending:-1}}]}),/invalid_operations_console/);
});
test('continuous workers retain their own sample times and independently enabled AI state',()=>{
 const send={state:'confirmed',reportedState:'confirmed',observedAt:90,fresh:true,running:true,enabled:true,statusReason:null};
 const agent={state:'outside_reply_window',reportedState:'outside_reply_window',observedAt:95,fresh:true,running:true,enabled:true,statusReason:null};
 const row={...market,setting:{...market.setting,automaticOperationsEnabled:false},lanes:{available:true,observedAt:90,continuousSend:send,agentReply:agent}};
 const result=validateOperationsConsole({...payload,markets:[row]}).markets[0];
 assert.equal(result.setting.automaticOperationsEnabled,false);
 assert.equal(result.lanes.continuousSend.state,'confirmed');assert.equal(result.lanes.continuousSend.observedAt,90);
 assert.equal(result.lanes.agentReply.enabled,true);assert.equal(result.lanes.agentReply.observedAt,95);
 assert.equal(result.lanes.agentReply.fresh,true);
});
test('dead, expired and legacy snapshots cannot remain actionable attention states',()=>{
 for(const extra of [{observedAt:9,fresh:false,running:false,statusReason:'worker_not_running'},
                     {observedAt:9,fresh:false,running:true,statusReason:'status_stale'},{}]){
  const row={...market,lanes:{...market.lanes,continuousSend:{state:'attention',...extra}}};
  const lane=validateOperationsConsole({...payload,markets:[row]}).markets[0].lanes.continuousSend;
  assert.equal(lane.state,'unverified');assert.equal(lane.reportedState,'attention');assert.equal(lane.fresh,false);
  assert.equal(lane.observedAt,9);
 }
});
test('a claimed fresh active lane needs a live worker and a recorded sample',()=>{
 for(const lane of [{state:'confirmed',fresh:true,running:false,observedAt:90},
                    {state:'confirmed',fresh:true,running:true,observedAt:null},
                    {state:'confirmed',fresh:'yes',running:true,observedAt:90}]){
  assert.throws(()=>validateOperationsConsole({...payload,markets:[{...market,lanes:{...market.lanes,observedAt:null,continuousSend:lane}}]}),/invalid_operations_console/);
 }
});
test('fresh explicit terminal receipts remain credible after their original worker exits',()=>{
 for(const state of ['attention','off','stopped','canary_confirmed']){
  const terminal={state,reportedState:state,fresh:true,running:false,enabled:true,observedAt:99,controlUpdatedAt:90,statusSource:'worker'};
  const lane=validateOperationsConsole({...payload,markets:[{...market,lanes:{...market.lanes,continuousSend:terminal}}]}).markets[0].lanes.continuousSend;
  assert.equal(lane.state,state);assert.equal(lane.fresh,true);assert.equal(lane.running,false);
 }
 for(const state of ['sending','completed','failed','disabled']){
  const terminal={state,fresh:true,running:false,observedAt:99,statusSource:'worker'};
  assert.throws(()=>validateOperationsConsole({...payload,markets:[{...market,lanes:{...market.lanes,continuousSend:terminal}}]}),/invalid_operations_console/);
 }
 for(const invalid of [{enabled:null},{controlUpdatedAt:null},{controlUpdatedAt:100}]){
  const terminal={state:'attention',fresh:true,running:false,enabled:true,observedAt:99,controlUpdatedAt:90,statusSource:'worker',...invalid};
  assert.throws(()=>validateOperationsConsole({...payload,markets:[{...market,lanes:{...market.lanes,continuousSend:terminal}}]}),/invalid_operations_console/);
 }
});
test('current disabled control evidence is distinct from the dead workers old sample',()=>{
 const disabled={state:'disabled',reportedState:'failed',fresh:true,running:false,enabled:false,observedAt:100,
  statusSource:'control',controlUpdatedAt:95,workerObservedAt:5};
 const validate=value=>validateOperationsConsole({...payload,markets:[{...market,lanes:{...market.lanes,agentReply:value}}]}).markets[0].lanes.agentReply;
 const lane=validate(disabled);
 assert.equal(lane.state,'disabled');assert.equal(lane.observedAt,100);assert.equal(lane.workerObservedAt,5);
 assert.equal(lane.controlUpdatedAt,95);assert.equal(lane.statusSource,'control');
 for(const invalid of [{enabled:null},{enabled:true},{running:true},{running:null},{controlUpdatedAt:null},{state:'attention'}]){
  assert.throws(()=>validate({...disabled,...invalid}),/invalid_operations_console/);
 }
});
