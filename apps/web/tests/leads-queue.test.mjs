import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateLeadsQueue,validateLeadsQueueRequest} from '../src/server/leads-queue/bridge.ts';

const config={version:'leads-queue-v1',refreshDays:7,leadsPerPid:10,windowDays:14,batchSize:1000,maxAttempts:3};
const payload={market:'it',config,refreshDays:7,eligible:2291,linked:1410,scope:1410,firstTime:216,due:0,waiting:1194,unknownScope:931,
 nextFirstTime:[{pid:'1729599424885136172',units:18700,title:'Canotta da uomo'}],
 nextDue:[],
 batchSize:1000,dueQueue:216,taken:216,batchFirst:216,batchRefresh:0,shortfall:784,padded:false,
 stuck:0,run:null};

test('a queue state is accepted and keeps the two queue sizes',()=>{
 const v=validateLeadsQueue(payload,'it');
 assert.equal(v.firstTime,216);
 assert.equal(v.waiting,1194);
 assert.equal(v.nextFirstTime[0].units,18700);
});

test('a batch shorter than the ceiling reports the shortfall instead of padding it',()=>{
 const v=validateLeadsQueue(payload,'it');
 assert.equal(v.batchSize,1000);
 assert.equal(v.taken,216);
 assert.equal(v.shortfall,784);
 assert.equal(v.padded,false);
 assert.equal(v.waiting,1194);
});

test('a queue that claims to have padded its batch is rejected outright',()=>{
 assert.throws(()=>validateLeadsQueue({...payload,padded:true},'it'),/invalid_leads_queue/);
 assert.throws(()=>validateLeadsQueue({...payload,padded:undefined},'it'),/invalid_leads_queue/);
});

test('a running batch is reported with its progress and zero platform writes',()=>{
 const v=validateLeadsQueue({...payload,run:{running:true,startedAt:1789400000,targets:200,done:12,leads:31,networkRequests:14,platformWrites:0,stopped:null}},'it');
 assert.equal(v.run.running,true);
 assert.equal(v.run.done,12);
 assert.equal(v.run.platformWrites,0);
});

test('a run that claims a platform write is rejected, because this path is a read',()=>{
 assert.throws(()=>validateLeadsQueue({...payload,run:{running:true,platformWrites:1}},'it'),/invalid_leads_queue/);
});

test('an empty queue is still a valid state',()=>{
 const v=validateLeadsQueue({...payload,scope:0,firstTime:0,waiting:0,nextFirstTime:[],nextDue:[],
  dueQueue:0,taken:0,batchFirst:0,batchRefresh:0,shortfall:1000},'it');
 assert.equal(v.scope,0);
 assert.equal(v.taken,0);
 assert.equal(v.shortfall,1000);
});

test('a due row keeps its clock so the page can show when it expires',()=>{
 const v=validateLeadsQueue({...payload,due:1,nextDue:[{pid:'1729599424885136172',queriedAt:1789400000,dueAt:1790004800,leads:7,title:'x'}]},'it');
 assert.equal(v.nextDue[0].leads,7);
 assert.equal(v.nextDue[0].dueAt,1790004800);
});

test('bad pids, negative counts and out-of-range ages are rejected',()=>{
 for(const bad of [{...payload,firstTime:-1},
                   {...payload,nextFirstTime:[{pid:'nope',units:1,title:''}]},
                   {...payload,nextFirstTime:[{pid:'1729599424885136172',units:-1,title:''}]},
                   {...payload,nextDue:[{pid:'1729599424885136172',queriedAt:'now',dueAt:1,leads:null,title:''}]},
                   {...payload,config:{...config,refreshDays:0}},
                   {...payload,config:{...config,refreshDays:91}},
                   {...payload,config:{...config,leadsPerPid:51}},
                   {...payload,config:{...config,windowDays:0}},
                   {...payload,config:{...config,batchSize:0}},
                   {...payload,config:{...config,batchSize:5001}},
                   {...payload,taken:-1},
                   {...payload,stuck:-1},
                   {...payload,config:{...config,maxAttempts:0}},
                   {...payload,run:{running:'yes'}}]){
  assert.throws(()=>validateLeadsQueue(bad,'it'),/invalid_leads_queue/);
 }
});

test('the refresh age is validated before it reaches the queue file',()=>{
 assert.deepEqual(validateLeadsQueueRequest({action:'save',market:'it',config}),{action:'save',market:'it',config});
 assert.deepEqual(validateLeadsQueueRequest({action:'run',market:'it'}),{action:'run',market:'it'});
 for(const bad of [{action:'stop',market:'it'},{action:'save'},{action:'save',market:'it',config:{...config,refreshDays:0}},
                   {action:'save',market:'it',config:{...config,batchSize:0}},null]){
  assert.throws(()=>validateLeadsQueueRequest(bad),/invalid_leads_queue_request/);
 }
});

test('rolling queue keeps A/B states, quota pause and automatic authorization separate',()=>{
 const a={total:3,runnable:0,first:1,refresh:1,checkpoints:1,states:{queued:2,backoff:1},oldestReadyAt:100,staleCheckpoints:1,oldestWindowEnd:'2026-09-01'};
 const b={total:2,runnable:0,first:1,refresh:0,checkpoints:0,states:{queued:1,material_paused:1},oldestReadyAt:200};
 const rolling={available:true,automaticEnabled:false,identityHold:{error:'old_identity_failure'},types:{A:a,B:b},control:{state:'waiting_quota',retry_at:1000,last_error:'kalodata_daily_quota_exhausted'}};
 const actual=validateLeadsQueue({...payload,rolling},'it').rolling;
 assert.equal(actual.automaticEnabled,false);
 assert.equal(actual.identityHold,true);
 assert.equal(actual.types.A.staleCheckpoints,1);
 assert.equal(actual.types.B.states.material_paused,1);
 assert.equal(actual.control.retry_at,1000);
 assert.throws(()=>validateLeadsQueue({...payload,rolling:{...rolling,types:{A:{...a,total:4},B:b}}},'it'),/invalid_leads_queue/);
});
test('a queued Kalodata stage is not running and the last finished result survives the round trip',()=>{
 const last={runId:'workflow-1',stageRunId:'stage-1',state:'completed',errorCode:null,startedAt:1,finishedAt:2,resultRecorded:true,
  completedQueries:7,aQueries:3,bQueries:4,fragments:8,networkRequests:11,sliceComplete:true,errorCount:1,errors:['kalodata_video_read_failed']};
 const run=validateLeadsQueue({...payload,run:{running:false,queued:true,startedAt:1,lastFinished:last,errors:[{pid:'1',code:'x'}]}},'it').run;
 assert.equal(run.running,false);assert.equal(run.queued,true);
 assert.deepEqual(run.lastFinished,last);assert.deepEqual(run.errors,[{pid:'1',code:'x'}]);
 const unrecorded={...last,state:'failed',resultRecorded:false,completedQueries:null,aQueries:null,bQueries:null,fragments:null,networkRequests:null,sliceComplete:null,errorCount:null,errors:[]};
 assert.equal(validateLeadsQueue({...payload,run:{running:false,lastFinished:unrecorded}},'it').run.lastFinished.completedQueries,null);
 assert.throws(()=>validateLeadsQueue({...payload,run:{running:false,lastFinished:{...last,aQueries:-1}}},'it'),/invalid_leads_queue/);
});
test('the last run carries what its publications added, and refuses a malformed contribution',()=>{
 const last={runId:'workflow-1',stageRunId:'stage-1',state:'completed',errorCode:null,startedAt:1,finishedAt:2,resultRecorded:true,
  completedQueries:7,aQueries:3,bQueries:4,fragments:8,networkRequests:11,sliceComplete:true,errorCount:0,errors:[],
  contribution:{publications:5,newPairs:40,refreshedPairs:12,newCreators:9,aPublications:3,bPublications:2}};
 assert.deepEqual(validateLeadsQueue({...payload,run:{running:false,lastFinished:last}},'it').run.lastFinished.contribution,last.contribution);
 assert.equal(validateLeadsQueue({...payload,run:{running:false,lastFinished:{...last,contribution:null}}},'it').run.lastFinished.contribution,null);
 assert.throws(()=>validateLeadsQueue({...payload,run:{running:false,lastFinished:{...last,contribution:{...last.contribution,newPairs:-1}}}},'it'),/invalid_leads_queue/);
});
