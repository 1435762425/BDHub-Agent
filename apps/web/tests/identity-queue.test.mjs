import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateIdentityConfig,validateIdentityQueue,validateIdentityQueueRequest} from '../src/server/identity-queue/bridge.ts';

const progress={startedAt:100,updatedAt:160,rounds:7,limit:2000,cohortSize:20,pendingAtStart:1035,
 pending:900,claimed:140,lastTargets:20,found:110,notFound:25,stopReason:null};

const payload_paused=()=>({...payload,run:{...payload.run,stopping:true}});

const payload={available:true,leads:9002,resolvedLeads:5314,resolvedCreators:1606,pendingLeads:1977,
 pendingCreators:1035,unresolvedLeads:1711,unresolvedCreators:700,
 pendingBreakdown:{unhanded:820,queued:1082,blocked:75},reconciled:true,
 policy:{qps:12,lanes:9,acceptanceId:'accept-ef6d3f880d256fe68deaab01818d',published:true,readable:true},
 config:{batchSize:2000,cohortSize:20},
 run:{name:'identity',label:'达人身份（OECID）',pid:1,startedAt:100,log:'x',
  config:{batchSize:2000,cohortSize:20},platformWrites:false,running:true,stopping:false,progress}};

test('the identity stage reports the three groups and the published channel',()=>{
 const v=validateIdentityQueue(payload);
 assert.equal(v.resolvedCreators,1606);
 assert.equal(v.pendingCreators,1035);
 assert.equal(v.unresolvedLeads,1711);
 assert.deepEqual(v.pendingBreakdown,{unhanded:820,queued:1082,blocked:75});
 assert.equal(v.policy.qps,12);
 assert.equal(v.policy.published,true);
 assert.equal(v.reconciled,true);
 assert.equal(v.config.cohortSize,20);
});

test('a running backfill reports its round and its winners',()=>{
 const v=validateIdentityQueue(payload);
 assert.equal(v.run.running,true);
 assert.equal(v.run.progress.rounds,7);
 assert.equal(v.run.progress.found,110);
 assert.equal(v.run.progress.stopReason,null);
 // Resolving an identity reads the platform profile; it must never claim a platform write.
 assert.equal(v.run.platformWrites,false);
 assert.equal(v.run.stopping,false);
});

test('a workspace with no identity work is reported as unavailable, not as zeroes that look real',()=>{
 const v=validateIdentityQueue({available:false,config:{batchSize:2000,cohortSize:20},run:null});
 assert.equal(v.available,false);
 assert.equal(v.resolvedCreators,0);
 assert.equal(v.config.batchSize,2000);
});

test('only the validated cohort sizes and a sane ceiling are accepted',()=>{
 assert.deepEqual(validateIdentityConfig({batchSize:50,cohortSize:10}),{batchSize:50,cohortSize:10});
 assert.deepEqual(validateIdentityConfig({batchSize:2000,cohortSize:50}),{batchSize:2000,cohortSize:50});
 for(const bad of [{batchSize:0,cohortSize:20},{batchSize:200001,cohortSize:20},{batchSize:10,cohortSize:1},
                   {batchSize:10,cohortSize:2},{batchSize:10,cohortSize:51},{batchSize:10,cohortSize:'20'},
                   {batchSize:10,cohortSize:20,typo:1},null]){
  assert.throws(()=>validateIdentityConfig(bad),/invalid_identity_queue/);
 }
});

test('a stop reason travels with the reason the round died, not alone',()=>{
 // `internal_error` 单独出现时无从下手：驱动抓到的异常类型必须跟着进度一起到页面。
 const failed={...progress,stopReason:'internal_error',
  errors:[{round:1,detail:'earlier'},{round:2,detail:'identity_worker_crash OperationalError -- traceback: var/identity-worker-crash.log'}]};
 const v=validateIdentityQueue({...payload,run:{...payload.run,running:false,progress:failed}});
 assert.equal(v.run.progress.stopReason,'internal_error');
 assert.deepEqual(v.run.progress.errors,[{round:1,detail:'earlier'},
  {round:2,detail:'identity_worker_crash OperationalError -- traceback: var/identity-worker-crash.log'}]);
 // 老进度文件没有这个字段：它是可选的历史，不能因此让整张卡读不出来。
 const older={...progress,stopReason:'internal_error'};
 assert.deepEqual(validateIdentityQueue({...payload,run:{...payload.run,progress:older}}).run.progress.errors,[]);
 for(const bad of [{...failed,errors:'locked'},{...failed,errors:[{round:1}]},{...failed,errors:[{round:1,detail:''}]}]){
  assert.throws(()=>validateIdentityQueue({...payload,run:{...payload.run,progress:bad}}),/invalid_identity_queue/);
 }
});

test('a retried round is visible as waiting, not as a stalled bar',()=>{
 // 账号库被占用时驱动会重试；页面上必须能看出"在等"和"卡死"的区别。
 const waiting={...progress,rounds:0,claimed:0,lastTargets:0,roundRunning:0,roundStartedAt:null,retry:2};
 const v=validateIdentityQueue({...payload,run:{...payload.run,running:true,progress:waiting}});
 assert.equal(v.run.progress.retry,2);
 assert.equal(v.run.progress.roundRunning,0);
 // 老进度文件没有 retry：缺字段按 0 读，不能因此拒绝整包。
 assert.equal(validateIdentityQueue({...payload,run:{...payload.run,progress}}).run.progress.retry,0);
 assert.throws(()=>validateIdentityQueue({...payload,run:{...payload.run,progress:{...progress,retry:-1}}}),
  /invalid_identity_queue/);
});

test('a half-written step is refused instead of drawn as a bar',()=>{
 for(const badProgress of [{...progress,pendingAtStart:-1},{...progress,rounds:1.5},
                           {...progress,found:undefined},{...progress,stopReason:7}]){
  assert.throws(()=>validateIdentityQueue({...payload,run:{...payload.run,progress:badProgress}}),/invalid_identity_queue/);
 }
 for(const badPayload of [{...payload,pendingBreakdown:undefined},
                          {...payload,resolvedCreators:-1},
                          {...payload,reconciled:'yes'},
                          {...payload,pendingBreakdown:{unhanded:1,queued:1}}]){
  assert.throws(()=>validateIdentityQueue(badPayload),/invalid_identity_queue/);
 }
 // A missing acceptance id is not a broken payload: it is an unpublished channel.
 const unpublished=validateIdentityQueue({...payload,policy:{qps:3,lanes:3}}).policy;
 assert.equal(unpublished.acceptanceId,null);
 assert.equal(unpublished.published,false);
 assert.equal(unpublished.readable,false);
 // Not reconciled is a legitimate state the page warns about, not a contract violation.
 assert.equal(validateIdentityQueue({...payload,reconciled:false}).reconciled,false);
});

test('a run record whose config no longer validates still renders',()=>{
 const v=validateIdentityQueue({...payload,run:{...payload.run,config:{batchSize:3,cohortSize:1}}});
 assert.equal(v.run.running,true);
 assert.equal(v.run.config.cohortSize,payload.config.cohortSize);
});

test('a stop request carries nothing but its intent',()=>{
 assert.deepEqual(validateIdentityQueueRequest({action:'stop'}),{action:'stop'});
 const stopped=validateIdentityQueue(payload_paused());
 assert.equal(stopped.run.running,true);
 assert.equal(stopped.run.stopping,true);
});

test('saving the ceiling, starting and stopping are the only three requests',()=>{
 assert.deepEqual(validateIdentityQueueRequest({action:'save',config:{batchSize:100,cohortSize:10}}),
  {action:'save',config:{batchSize:100,cohortSize:10}});
 assert.deepEqual(validateIdentityQueueRequest({action:'start',config:{batchSize:2000,cohortSize:20}}),
  {action:'start',config:{batchSize:2000,cohortSize:20}});
 for(const bad of [{action:'start'},{action:'stop',config:{batchSize:1,cohortSize:1}},
                   {action:'start',config:{batchSize:1,cohortSize:7}},null]){
  assert.throws(()=>validateIdentityQueueRequest(bad),/invalid_identity_queue_request/);
 }
});

test('the identity card counts creators, and its three classes must add up',()=>{
 // 这张卡只以**达人（去重 handle）**为单位：一个达人名下可以挂很多条线索，
 // 所以三个分类必须互斥，否则同一个达人会在两处各算一次（实测会多算 1,100+）。
 const by={handles:3069,resolved:2074,unresolved:976,blocked:19,unknown:0,
  blockedReasons:[{reason:'request_or_signer_error',count:19}],leads:12996,positions:6967,reconciled:true};
 const v=validateIdentityQueue({...payload,byCreator:by});
 assert.deepEqual(v.byCreator,by);
 assert.equal(v.byCreator.resolved+v.byCreator.unresolved+v.byCreator.blocked+v.byCreator.unknown,v.byCreator.handles);
 // 可达位置是达人×商品：一位达人有了 OECID，他名下的所有线索商品都成为位置。
 assert.ok(v.byCreator.positions>=v.byCreator.resolved);
 // 相加对不上 → 整包坏，不画一个自己都对不平的总数。
 for(const bad of [{...by,unknown:20},{...by,blocked:18},{...by,reconciled:false},{...by,handles:undefined},
                   {...by,resolved:-1},{...by,positions:'6967'},
                   {...by,blockedReasons:[{reason:'',count:1}]},{...by,blockedReasons:[{reason:'x',count:-1}]},
                   {...by,blockedReasons:'none'}]){
  assert.throws(()=>validateIdentityQueue({...payload,byCreator:bad}),/invalid_identity_queue/);
 }
 // 老载荷没有这一块：按"没有"读，不能因此让整张卡消失。
 assert.equal(validateIdentityQueue(payload).byCreator,null);
});
