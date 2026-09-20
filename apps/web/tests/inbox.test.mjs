import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseInboxQuery,validateInbox,validateInboxConfig,validateInboxDayDetail,validateInboxRequest} from '../src/server/inbox/bridge.ts';
const url='http://127.0.0.1:5198/api/inbox';

const step={processed:6,added:0,historical:0,liveReplies:0,indexedTargets:555,serviceDecisions:0,
 errorCode:null,state:'',checkedAt:1789472601.7,conversations:555,events:1352,historicalEvents:316,
 gaps:0,pendingContent:25,lastCheckedAt:1789472601.7,automaticRepliesEnabled:false};

const day={date:'2026-09-15',cards:0,texts:0,creators:0,unconfirmed:0,replies:0,showcase:0,
 ourMessages:0,autoReplies:0,casesOpened:0};
const totals=(({date,...rest})=>rest)(day);

const payload={available:true,config:{limit:6,interval:60},configInvalid:false,
 run:{name:'inbox',label:'收信监控',pid:65051,startedAt:1789472348.9,log:'x',
  config:{limit:6,interval:60},platformWrites:false,running:true,stopping:false,progress:step},
 today:day,openCases:1,timezone:'Asia/Shanghai',totals,days:[day]};

test('the monitor card reads the job, today and the day list from one payload',()=>{
 const v=validateInbox(payload);
 assert.equal(v.available,true);
 assert.equal(v.run.running,true);
 assert.equal(v.run.progress.indexedTargets,555);
 assert.equal(v.run.progress.conversations,555);
 assert.equal(v.today.date,'2026-09-15');
 assert.equal(v.today.replies,0);
 assert.equal(v.openCases,1);
 assert.equal(v.timezone,'Asia/Shanghai');
 assert.deepEqual(v.totals,totals);
 assert.equal(v.days.length,1);
 // 收信只读平台；一个说它写过平台的回答是坏包，不是状态。
 assert.equal(v.run.platformWrites,false);
});

test('an unavailable workspace is reported as unavailable, not as zeroes that look real',()=>{
 const v=validateInbox({available:false,config:{limit:6,interval:60},run:null});
 assert.equal(v.available,false);
 assert.equal(v.today,null);
 assert.equal(v.days.length,0);
 assert.equal(v.totals.replies,0);
 // 但配置仍然要能读出来：操作者还得能改它。
 assert.deepEqual(v.config,{limit:6,interval:60});
});

test('only the script\'s own pacing bounds are accepted',()=>{
 assert.deepEqual(validateInboxConfig({limit:1,interval:30}),{limit:1,interval:30});
 assert.deepEqual(validateInboxConfig({limit:12,interval:3600}),{limit:12,interval:3600});
 for(const bad of [{limit:0,interval:60},{limit:13,interval:60},{limit:6,interval:29},
                   {limit:6,interval:3601},{limit:6,interval:60,typo:1},{limit:'6',interval:60},null]){
  assert.throws(()=>validateInboxConfig(bad),/invalid_inbox/);
 }
});

test('a round that read nothing must say why, and only in known shapes',()=>{
 const busy=validateInbox({...payload,run:{...payload.run,progress:{...step,errorCode:'live_guard_busy'}}});
 assert.equal(busy.run.progress.errorCode,'live_guard_busy');
 // 原因要么是字符串要么是空；数字不是"另一个原因"，是另一个载荷。
 assert.throws(()=>validateInbox({...payload,run:{...payload.run,progress:{...step,errorCode:7}}}),
  /invalid_inbox/);
 assert.throws(()=>validateInbox({...payload,run:{...payload.run,progress:{...step,checkedAt:-1}}}),
  /invalid_inbox/);
 assert.throws(()=>validateInbox({...payload,run:{...payload.run,progress:{...step,liveReplies:'0'}}}),
  /invalid_inbox/);
});

test('a half-written day is refused instead of drawn on a calendar',()=>{
 for(const bad of [{...day,date:'2026/09/15'},{...day,date:'15-09-2026'},{...day,cards:-1},
                   {...day,replies:undefined}]){
  assert.throws(()=>validateInbox({...payload,days:[bad],today:bad}),/invalid_inbox/);
 }
 // 同一天出现两次会让日历把两行加成一个假数字。
 assert.throws(()=>validateInbox({...payload,days:[day,day]}),/invalid_inbox/);
 assert.throws(()=>validateInbox({...payload,timezone:''}),/invalid_inbox/);
 assert.throws(()=>validateInbox({...payload,days:'x'}),/invalid_inbox/);
 assert.throws(()=>validateInbox({...payload,openCases:-1}),/invalid_inbox/);
});

test('period totals and today must equal the day rows',()=>{
 assert.throws(()=>validateInbox({...payload,totals:{...totals,replies:1}}),/invalid_inbox/);
 assert.throws(()=>validateInbox({...payload,today:{...day,showcase:1}}),/invalid_inbox/);
 assert.throws(()=>validateInbox({...payload,today:{...day,date:'2026-09-16'}}),/invalid_inbox/);
});

test('a run record whose config no longer validates still renders',()=>{
 const v=validateInbox({...payload,run:{...payload.run,config:{limit:99,interval:5}}});
 assert.equal(v.run.running,true);
 assert.deepEqual(v.run.config,payload.config);
});

test('the three actions carry nothing more than they need',()=>{
 assert.deepEqual(validateInboxRequest({action:'save',config:{limit:6,interval:60}}),
  {action:'save',config:{limit:6,interval:60}});
 assert.deepEqual(validateInboxRequest({action:'start',config:{limit:6,interval:60}}),
  {action:'start',config:{limit:6,interval:60}});
 assert.deepEqual(validateInboxRequest({action:'stop'}),{action:'stop'});
 // 停止不需要参数；夹带配置的是坏请求，不是停止。
 for(const bad of [{action:'stop',config:{limit:6,interval:60}},{action:'start'},
                   {action:'start',config:{limit:6,interval:5}},{action:'run'},null]){
  assert.throws(()=>validateInboxRequest(bad),/invalid_inbox_request/);
 }
});

test('a run record for a different job cannot wear this shape',()=>{
 // 别的作业的运行记录长得像这份载荷，那也一样是错的：卡片会拿它去读收信的进度。
 for(const bad of [{...payload.run,name:'identity'},{...payload.run,running:'yes'},
                   {...payload.run,pid:'65051'},{...payload.run,label:null}]){
  assert.throws(()=>validateInbox({...payload,run:bad}),/invalid_inbox/);
 }
 // 只读监控永远不报写入；状态只读，所以这里只断言它被如实读出。
 assert.equal(validateInbox(payload).run.platformWrites,false);
});

test('day-detail queries are exact, bounded and cannot smuggle another command',()=>{
 assert.deepEqual(parseInboxQuery(url),{view:'status',days:14});
 assert.deepEqual(parseInboxQuery(url+'?days=30'),{view:'status',days:30});
 assert.deepEqual(parseInboxQuery(url+'?date=2026-09-15'),
  {view:'detail',date:'2026-09-15',offset:0,limit:50});
 assert.deepEqual(parseInboxQuery(url+'?date=2026-09-15&offset=50&limit=100'),
  {view:'detail',date:'2026-09-15',offset:50,limit:100});
 for(const query of ['?date=2026-02-30','?date=2026-09-15&limit=0','?date=2026-09-15&limit=101',
  '?date=2026-09-15&offset=5001','?date=2026-09-15&date=2026-09-16','?date=2026-09-15&action=start'])
  assert.throws(()=>parseInboxQuery(url+query),/invalid_inbox_query/);
});

test('day-detail decoder keeps only bounded read-only rows and reconciles its total',()=>{
 const summary={...day,cards:1,replies:1};
 const items=[
  {kind:'delivery',occurredAt:1789472601000,ref:'delivery-1',creatorId:'creator-1',oec:'123',handle:'new_name',handleAtEvent:'old_name',pid:'1729480019490150432',status:'confirmed',product:'prodotto',creatorPercent:'13',catalogSource:'selected',text:'Ciao!',format:'text',textState:'confirmed'},
  {kind:'reply',occurredAt:1789472602000,ref:'message-1',creatorId:'creator-1',oec:'123',handle:'new_name',handleAtEvent:null,pid:null,status:'creatorReplies',product:null,creatorPercent:null,catalogSource:null,text:'Grazie!',format:'text',textState:null},
 ];
 const result=validateInboxDayDetail({available:true,date:day.date,timezone:'Asia/Shanghai',summary,
  total:2,offset:0,limit:50,nextOffset:null,items,platformWrites:false});
 assert.equal(result.items[0].handleAtEvent,'old_name');
 assert.equal(result.items[1].text,'Grazie!');
 assert.equal(result.platformWrites,false);
 for(const bad of [
  {total:3},
  {platformWrites:true},
  {nextOffset:1},
  {items:[{...items[0],kind:'raw_payload'}]},
  {items:[{...items[0],text:'x'.repeat(4001)}]},
 ])assert.throws(()=>validateInboxDayDetail({available:true,date:day.date,timezone:'Asia/Shanghai',summary,
  total:2,offset:0,limit:50,nextOffset:null,items,platformWrites:false,...bad}),/invalid_inbox_detail/);
});
