import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateJobs,validateJobsSave} from '../src/server/jobs/bridge.ts';

const job={id:'catalog_collect',name:'商品发现 / 刷新',group:'货盘',description:'逐页采集。',manual:'global-source-sync',manualEndpoint:'/api/global-source',lastRunAt:1789318884,enabled:false,at:'03:00'};
const unwired={...job,id:'creator_leads',name:'达人线索查询（Kalodata）',group:'达人',manual:'unwired',manualEndpoint:null,lastRunAt:null,at:'04:00'};
const payload={version:'jobs-v1',jobs:[job,unwired],schedulerReady:false};

test('a jobs state is accepted and keeps the manual endpoint and schedule intent',()=>{
 const v=validateJobs(payload);
 assert.equal(v.jobs[0].manualEndpoint,'/api/global-source');
 assert.equal(v.jobs[0].enabled,false);
 assert.equal(v.jobs[0].at,'03:00');
 assert.equal(v.jobs[1].manualEndpoint,null);
 assert.equal(v.schedulerReady,false);
});

test('a job with no verified trigger reports no endpoint rather than a guessed one',()=>{
 const v=validateJobs(payload);
 assert.equal(v.jobs[1].manual,'unwired');
 assert.equal(v.jobs[1].manualEndpoint,null);
 assert.equal(v.jobs[1].lastRunAt,null);
});

test('bad times, bad ids and duplicate jobs are rejected',()=>{
 for(const bad of [{...payload,jobs:[{...job,at:'25:00'}]},
                   {...payload,jobs:[{...job,at:'3:00'}]},
                   {...payload,jobs:[{...job,id:'Bad Id'}]},
                   {...payload,jobs:[{...job,enabled:'yes'}]},
                   {...payload,jobs:[{...job,lastRunAt:'yesterday'}]},
                   {...payload,jobs:[]},
                   {...payload,jobs:[job,job]},
                   {...payload,version:''}]){
  assert.throws(()=>validateJobs(bad),/invalid_jobs/);
 }
});

test('a save request carries only the fields it means to change',()=>{
 assert.deepEqual(validateJobsSave({action:'save',jobs:{catalog_collect:{enabled:true}}}),
  {jobs:{catalog_collect:{enabled:true}}});
 assert.deepEqual(validateJobsSave({action:'save',jobs:{catalog_collect:{at:'03:30'}}}),
  {jobs:{catalog_collect:{at:'03:30'}}});
 assert.deepEqual(validateJobsSave({action:'save',jobs:{catalog_collect:{at:null}}}),
  {jobs:{catalog_collect:{at:null}}});
 for(const bad of [{action:'save'},{action:'run',jobs:{catalog_collect:{enabled:true}}},
                   {action:'save',jobs:{catalog_collect:{}}},
                   {action:'save',jobs:{catalog_collect:{at:'99:99'}}},
                   {action:'save',jobs:{catalog_collect:{enabled:1}}},
                   {action:'save',jobs:{'bad id':{enabled:true}}},
                   {action:'save',jobs:{}},
                   null]){
  assert.throws(()=>validateJobsSave(bad),/invalid_jobs_request/);
 }
});
