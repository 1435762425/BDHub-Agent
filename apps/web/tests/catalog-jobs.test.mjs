import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateCatalogJobs,validateCatalogJobsRequest} from '../src/server/catalog-jobs/bridge.ts';

const payload={selection:{label:'全托商品选入',config:{limit:200},run:null},
 links:{label:'链接准备',config:{readLimit:15,creates:0,lanes:9,qps:12},run:null},
 // 非全托链接准备是**独立的作业名**：渠道由名字钉死，所以两条渠道不会互相带错渠道。
 linksCampaign:{label:'非全托链接准备',config:{readLimit:15,creates:0,lanes:9,qps:12},run:null},
 // 非全托采集也是独立作业名：只读平台，读完之后自动重新筛分。
 campaignCollect:{label:'非全托采集',config:{maxRequests:150,passes:40},run:null}};

test('every catalogue job is accepted before anything has run',()=>{
 const v=validateCatalogJobs(payload);
 assert.equal(v.selection.config.limit,200);
 assert.equal(v.links.config.creates,0);
 assert.equal(v.linksCampaign.config.creates,0);
 assert.equal(v.campaignCollect.config.maxRequests,150);
 assert.equal(v.selection.run,null);
});

test('a read-only link run is not reported as a platform write',()=>{
 const v=validateCatalogJobs({...payload,links:{...payload.links,run:{name:'links',label:'链接准备',pid:1,startedAt:1,log:'x',config:{readLimit:15,creates:0},platformWrites:false,running:true}}});
 assert.equal(v.links.run.running,true);
 assert.equal(v.links.run.platformWrites,false);
});

test('out-of-range limits and unknown fields are refused before the job is launched',()=>{
 for(const bad of [{...payload,selection:{...payload.selection,config:{limit:601}}},
                   {...payload,selection:{...payload.selection,config:{limit:0}}},
                   {...payload,links:{...payload.links,config:{readLimit:15,creates:201}}},
                   {...payload,links:{...payload.links,config:{readLimit:15,creates:-1}}},
                   {...payload,links:{...payload.links,config:{readLimit:15,creates:1,typo:2}}},
                   // Lanes and rate are a closed set, not a range: 5 lanes is not a valid reader.
                   {...payload,links:{...payload.links,config:{readLimit:15,creates:0,lanes:5,qps:12}}},
                   {...payload,links:{...payload.links,config:{readLimit:15,creates:0,lanes:9,qps:7}}},
                   {...payload,selection:null}]){
  assert.throws(()=>validateCatalogJobs(bad),/invalid_catalog_jobs/);
 }
 // 非全托链接作业自己也要按同一套边界校验：漏一个字段就是坏请求，不是一个能跑的任务。
 for(const bad of [201,-1]){
  assert.throws(()=>validateCatalogJobs({...payload,linksCampaign:{...payload.linksCampaign,config:{readLimit:15,creates:bad}}}),
   /invalid_catalog_jobs/);
 }
});

test('a run record whose config no longer validates still renders',()=>{
 // Lanes were once a free number; a stored run from before the closed set must not blank the panel.
 const v=validateCatalogJobs({...payload,links:{...payload.links,run:{...run,
  config:{readLimit:15,creates:0,lanes:5,qps:12}}}});
 assert.equal(v.links.run.running,true);
 assert.equal(v.links.run.config.lanes,payload.links.config.lanes);
});

test('a start request names one of the jobs and may carry its config',()=>{
 assert.deepEqual(validateCatalogJobsRequest({action:'start',name:'selection'}),{action:'start',name:'selection'});
 // 非全托链接作业必须能启动，且它没有 route 字段可传（渠道由作业名决定）。
 assert.deepEqual(validateCatalogJobsRequest({action:'start',name:'linksCampaign',config:{creates:0}}),
  {action:'start',name:'linksCampaign',config:{creates:0}});
 assert.throws(()=>validateCatalogJobsRequest({action:'start',name:'linksCampaign',config:{route:'campaign'}}),
  /invalid_catalog_jobs_request/);
 assert.deepEqual(validateCatalogJobsRequest({action:'start',name:'links',config:{creates:5}}),
  {action:'start',name:'links',config:{creates:5}});
 assert.deepEqual(validateCatalogJobsRequest({action:'save',name:'links',config:{readLimit:20,creates:0}}),
  {action:'save',name:'links',config:{readLimit:20,creates:0}});
 // stop 现在是一条正式动作（作业在下一个安全点退出）。
 assert.deepEqual(validateCatalogJobsRequest({action:'stop',name:'linksCampaign'}),{action:'stop',name:'linksCampaign'});
 for(const bad of [{action:'stop',name:'nope'},{action:'stop',name:'links',config:{creates:1}},{action:'start',name:'nope'},{action:'start'},
                   {action:'start',name:'links',config:{creates:999}},null]){
  assert.throws(()=>validateCatalogJobsRequest(bad),/invalid_catalog_jobs_request/);
 }
});

const run={name:'links',label:'链接准备',pid:1,startedAt:1,log:'x',config:{readLimit:15,creates:0},platformWrites:false,running:true};

test('a running batch reports the step it is on',()=>{
 const progress={phase:'read',step:4,pass:3,passes:80,created:0,updatedAt:160,startedAt:100,total:3450,
  states:{pending:2216,ready:1098,reuse:1191,review:1161}};
 const v=validateCatalogJobs({...payload,links:{...payload.links,run:{...run,progress}}});
 assert.equal(v.links.run.progress.phase,'read');
 assert.equal(v.links.run.progress.pass,3);
 assert.equal(v.links.run.progress.states.pending,2216);
});

test('a job that has not published a step yet reports no progress',()=>{
 const v=validateCatalogJobs({...payload,links:{...payload.links,run:{...run,progress:null}}});
 assert.equal(v.links.run.progress,null);
 assert.equal(validateCatalogJobs(payload).links.run,null);
});

test('a half-written progress payload is refused instead of drawn as a bar',()=>{
 for(const bad of [{...run,progress:{phase:'read',step:4,pass:3,passes:80,created:0,updatedAt:1,startedAt:1,total:3450}},
                   {...run,progress:{phase:'read',step:4,pass:3,passes:80,created:0,updatedAt:1,startedAt:1,total:3450,states:{ready:-1}}},
                   {...run,progress:{phase:'read',step:4,pass:3,passes:80,created:0,updatedAt:1,startedAt:1,total:3450,states:{ready:1.5}}}]){
  assert.throws(()=>validateCatalogJobs({...payload,links:{...payload.links,run:bad}}),/invalid_catalog_jobs/);
 }
});
