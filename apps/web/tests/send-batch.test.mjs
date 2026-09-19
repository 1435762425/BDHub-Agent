import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateSendRequest,validateSendState} from '../src/server/send/bridge.ts';

const sample={handle:'nuvolablu4',pid:'1729571380453480878',name:'questi orecchini per cartilagine',
 nameZh:'软骨耳环',nameSource:'缓存',
 messageIt:'Ciao @nuvolablu4! Abbiamo una commissione migliorata al 13% per te su questi orecchini per cartilagine 👏 Ti va di dedicarci un nuovo video o LIVE?',
 messageZh:'你好！这款软骨耳环可以为你提供更高的 13% 佣金，下一条视频或直播可以再推一轮。',template:'standard',
 creatorPercent:'13',publicPercent:'12',campaignId:'7667413079080240918',catalogSource:'selected',unlocked:false};

const payload={available:true,
 config:{count:500,widen:false,windowEnabled:false,window:['09:00','24:00']},
 preview:{available:true,requested:500,sendable:500,positions:1769,readyAvailable:1769,
  samples:[sample],nameQuality:{缓存:476,取自卡名:24},
  skipped:{missing_card:797,offer_not_in_current_catalog:217,relationship_blocked:7,beyond_requested_size:248},
  capacity:{windowSeconds:86400,limit:500,used:0,remaining:500},
  window:{enabled:false,open:true,start:null,end:null},widen:false},
 pool:{counts:{positions:6980},layers:{ready:1769,queued:3378,cooling:1333,awaiting_reply:3,excluded:2,sent:495}}};

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
 const v=validateSendState({available:false,config:payload.config,
  preview:{available:false,requested:0,skipped:{},window:{enabled:false,open:true,start:null,end:null}}});
 assert.equal(v.available,false);
 assert.equal(v.preview.sendable,0);
 // 但配置仍然要能读出来：操作者还得能改它。
 assert.deepEqual(v.config,payload.config);
});

test('a batch that does not add up is refused instead of shown as real numbers',()=>{
 // 池子是个划分：每个扫到的槽位要么进这一批、要么有具名原因。少算一条就必须整包拒。
 const broken=structuredClone(payload);
 broken.preview.skipped.beyond_requested_size=247;
 assert.throws(()=>validateSendState(broken),/invalid_send/);
 const swapped=structuredClone(payload);
 swapped.preview.sendable=499;
 assert.throws(()=>validateSendState(swapped),/invalid_send/);
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

test('only the three settings can be saved, and only the released sizes',()=>{
 assert.deepEqual(validateSendRequest({action:'save',config:{count:1000}}).config.count,1000);
 assert.deepEqual(validateSendRequest({action:'save',config:{count:500,widen:true,windowEnabled:true,
  window:['09:00','24:00']}}).config.window,['09:00','24:00']);
 // 600 是越界探测档：只在同时开着越界时才接受。
 assert.equal(validateSendRequest({action:'save',config:{count:600,widen:true}}).config.count,600);
 assert.throws(()=>validateSendRequest({action:'save',config:{count:600}}),/invalid_send_request/);
 assert.throws(()=>validateSendRequest({action:'save',config:{count:777}}),/invalid_send_request/);
 assert.throws(()=>validateSendRequest({action:'save',config:{count:500,unknown:1}}),/invalid_send_request/);
 assert.throws(()=>validateSendRequest({action:'start',config:{count:500}}),/invalid_send_request/);
 assert.throws(()=>validateSendRequest({action:'save',config:{count:500},extra:1}),/invalid_send_request/);
 // 窗口格式要在这一层挡住，不能等 Python 抛。
 assert.throws(()=>validateSendRequest({action:'save',config:{count:500,window:['9:00','24:00']}}),/invalid_send/);
 assert.throws(()=>validateSendRequest({action:'save',config:{count:500,window:['09:00','25:00']}}),/invalid_send/);
});
