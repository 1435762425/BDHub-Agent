import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateSendRequest,validateSendState} from '../src/server/send/bridge.ts';

const sample={handle:'nuvolablu4',oecId:'1234567890123456789',pid:'1729571380453480878',sourceClass:'A',sourceRank:1,units:12,gmv:'450.5',videoViews:null,videoId:null,videoReleasedAt:null,name:'questi orecchini per cartilagine',
 nameZh:'软骨耳环',nameSource:'缓存',
 messageIt:'Ciao @nuvolablu4! Abbiamo una commissione migliorata al 13% per te su questi orecchini per cartilagine 👏 Ti va di dedicarci un nuovo video o LIVE?',
 messageZh:'你好！这款软骨耳环可以为你提供更高的 13% 佣金，下一条视频或直播可以再推一轮。',template:'standard',
 creatorPercent:'13',publicPercent:'12',campaignId:'7667413079080240918',catalogSource:'selected',currentListId:'1111111111111111111',unlocked:false};
const hash='a'.repeat(64);
const authorization={source:'current_user_request',scope:'pool_to_send',maxPeople:500,requestedPeople:500,
 reservePeople:50,frozenPeople:550,reservePolicy:'ceil-10-percent-v1',
 widenLocalGate:false,sendWindow:null,institutionNewContactRollingCap:500,
 materialPolicy:'frozen-current-binding-v1',note:'只消费本批冻结位置'};

const payload={market:'it',account:'acc6',available:true,
 config:{count:500,widen:false,windowEnabled:false,window:['09:00','24:00']},
 preview:{available:true,requested:500,reserveRequested:50,required:550,sendable:500,reserveReady:50,
  frozenTotal:550,fullPreparation:true,positions:1769,readyAvailable:1769,
  samples:[sample],nameQuality:{缓存:476,取自卡名:24},
  skipped:{missing_card:797,offer_not_in_current_catalog:217,relationship_blocked:7,beyond_requested_size:198},
  capacity:{windowSeconds:86400,limit:500,used:0,remaining:500},
  window:{enabled:false,open:true,start:null,end:null},widen:false,previewHash:hash,authorization},
 pool:{counts:{positions:6980},layers:{ready:1769,queued:3378,cooling:1333,awaiting_reply:3,excluded:2,sent:495}},batch:null};

test('the send card reads the batch preview and the pool layers from one payload',()=>{
 const v=validateSendState(payload);
 assert.equal(v.available,true);
 assert.equal(v.preview.sendable,500);
 assert.equal(v.preview.readyAvailable,1769);
 assert.deepEqual(v.preview.skipped,payload.preview.skipped);
 assert.deepEqual(v.preview.nameQuality,{缓存:476,取自卡名:24});
 assert.equal(v.preview.samples[0].nameSource,'缓存');
 // 样例要带**发给达人的那句话**：页面展示的就是它，不能再拿内部中文短名顶替。
 assert.match(v.preview.samples[0].messageIt,/^Ciao @nuvolablu4!/);
 assert.equal(v.preview.samples[0].nameZh,'软骨耳环');
 assert.equal(v.preview.samples[0].template,'standard');
 assert.equal(v.preview.capacity.remaining,500);
 assert.deepEqual(v.pool.layers,payload.pool.layers);
 assert.deepEqual(v.config,payload.config);
});

test('an unavailable pool is reported as unavailable, not as zeroes that look real',()=>{
 const v=validateSendState({market:'it',account:'acc6',available:false,config:payload.config,
  preview:{available:false,requested:0,skipped:{},window:{enabled:false,open:true,start:null,end:null}}});
 assert.equal(v.available,false);
 assert.equal(v.preview.sendable,0);
 // 但配置仍然要能读出来：操作者还得能改它。
 assert.deepEqual(v.config,payload.config);
});

test('send state is pinned to the enabled Italy market and ACC6 sender',()=>{
 assert.throws(()=>validateSendState({...payload,market:'mx'}),/invalid_send/);
 assert.throws(()=>validateSendState({...payload,account:'acc9'}),/invalid_send/);
});

test('a batch that does not add up is refused instead of shown as real numbers',()=>{
 // 池子是个划分：每个扫到的槽位要么进这一批、要么有具名原因。少算一条就必须整包拒。
 const broken=structuredClone(payload);
 broken.preview.skipped.beyond_requested_size=247;
 assert.throws(()=>validateSendState(broken),/invalid_send/);
 const swapped=structuredClone(payload);
 swapped.preview.sendable=499;
 assert.throws(()=>validateSendState(swapped),/invalid_send/);
 const reserve=structuredClone(payload);
 reserve.preview.reserveRequested=49;
 assert.throws(()=>validateSendState(reserve),/invalid_send/);
});

test('a sample without the message text is still readable, not fatal',()=>{
 // 老回答或渲染失败时不能整包拒：页面会自己说"这句话渲染不出来"。
 const bare=structuredClone(payload);
 delete bare.preview.samples[0].messageIt;
 delete bare.preview.samples[0].nameZh;
 const v=validateSendState(bare);
 assert.equal(v.preview.samples[0].messageIt,'');
 assert.equal(v.preview.samples[0].nameZh,'');
});

test('an unknown pool layer or a bad sample is refused',()=>{
 const layer=structuredClone(payload);
 layer.pool.layers.something_new=1;
 assert.throws(()=>validateSendState(layer),/invalid_send/);
 const pid=structuredClone(payload);
 pid.preview.samples[0].pid='172957138045348087';
 assert.throws(()=>validateSendState(pid),/invalid_send/);
 const many=structuredClone(payload);
 many.preview.samples=[sample,sample,sample,sample,sample,sample];
 assert.throws(()=>validateSendState(many),/invalid_send/);
});

test('an exact target from 1 to 2000 can be saved; presets are only shortcuts',()=>{
 assert.deepEqual(validateSendRequest({action:'save',config:{count:1000}}).config.count,1000);
 assert.deepEqual(validateSendRequest({action:'save',config:{count:500,widen:true,windowEnabled:true,
  window:['09:00','24:00']}}).config.window,['09:00','24:00']);
 assert.equal(validateSendRequest({action:'save',config:{count:600,widen:true}}).config.count,600);
 assert.equal(validateSendRequest({action:'save',config:{count:600}}).config.count,600);
 assert.equal(validateSendRequest({action:'save',config:{count:777}}).config.count,777);
 assert.throws(()=>validateSendRequest({action:'save',config:{count:0}}),/invalid_send_request/);
 assert.throws(()=>validateSendRequest({action:'save',config:{count:2001}}),/invalid_send_request/);
 assert.throws(()=>validateSendRequest({action:'save',config:{count:500,unknown:1}}),/invalid_send_request/);
 assert.throws(()=>validateSendRequest({action:'start',config:{count:500}}),/invalid_send_request/);
 assert.throws(()=>validateSendRequest({action:'save',config:{count:500},extra:1}),/invalid_send_request/);
 // 窗口格式要在这一层挡住，不能等 Python 抛。
 assert.throws(()=>validateSendRequest({action:'save',config:{count:500,window:['9:00','24:00']}}),/invalid_send/);
 assert.throws(()=>validateSendRequest({action:'save',config:{count:500,window:['09:00','25:00']}}),/invalid_send/);
});

test('freeze start and stop have exact explicit request shapes',()=>{
 assert.deepEqual(validateSendRequest({action:'freeze',requestId:'web-request-0001',expectedPreviewHash:hash}),
  {action:'freeze',requestId:'web-request-0001',expectedPreviewHash:hash});
 assert.deepEqual(validateSendRequest({action:'start',batchId:'send-batch-0001',expectedRevision:1,confirmed:true}),
  {action:'start',batchId:'send-batch-0001',expectedRevision:1,confirmed:true});
 assert.deepEqual(validateSendRequest({action:'stop',batchId:'send-batch-0001',expectedRevision:2}),
  {action:'stop',batchId:'send-batch-0001',expectedRevision:2});
 assert.deepEqual(validateSendRequest({action:'reconcile',batchId:'send-batch-0001',deliveryId:'delivery-0001',expectedRevision:2,confirmed:true}),
  {action:'reconcile',batchId:'send-batch-0001',deliveryId:'delivery-0001',expectedRevision:2,confirmed:true});
 assert.throws(()=>validateSendRequest({action:'start',batchId:'send-batch-0001',expectedRevision:1,confirmed:false}),/invalid_send_request/);
 assert.throws(()=>validateSendRequest({action:'freeze',requestId:'web-request-0001',expectedPreviewHash:hash,extra:1}),/invalid_send_request/);
 assert.throws(()=>validateSendRequest({action:'stop',batchId:'send-batch-0001',expectedRevision:2,confirmed:true}),/invalid_send_request/);
 assert.throws(()=>validateSendRequest({action:'reconcile',batchId:'send-batch-0001',deliveryId:'delivery-0001',expectedRevision:2,confirmed:false}),/invalid_send_request/);
});

test('a frozen batch response is validated and reconciles its item counts',()=>{
 const frozen=structuredClone(payload);
 frozen.batch={batchId:'send-batch-0001',requestId:'web-request-0001',previewHash:hash,revision:1,
  state:'prepared',target:500,attempted:500,reserveTotal:50,reservePromoted:0,reserveRemaining:50,
  counts:{pending:500},config:payload.config,authorization,
  authorizedAt:null,stopRequestedAt:null,createdAt:1789838000,unknownDeliveries:[]};
 assert.equal(validateSendState(frozen).batch.state,'prepared');
 frozen.batch.counts.pending=499;
 assert.throws(()=>validateSendState(frozen),/invalid_send/);
});

test('unknown delivery summary exposes only bounded original intent identifiers',()=>{
 const frozen=structuredClone(payload);
 frozen.batch={batchId:'send-batch-0001',requestId:'web-request-0001',previewHash:hash,revision:2,
  state:'waiting_reconciliation',target:500,attempted:500,reserveTotal:50,reservePromoted:0,reserveRemaining:50,
  counts:{sending:500},config:payload.config,authorization,authorizedAt:1789838000,stopRequestedAt:null,
  createdAt:1789838000,unknownDeliveries:[{deliveryId:'delivery-0001',creatorId:'creator-0001',
   oecId:'1234567890123456789',pid:'1729571380453480878',parts:{card:'unknown',text:'ready'}}]};
 assert.equal(validateSendState(frozen).batch.unknownDeliveries[0].parts.card,'unknown');
 frozen.batch.unknownDeliveries[0].parts.raw_payload='private';
 assert.throws(()=>validateSendState(frozen),/invalid_send/);
});
