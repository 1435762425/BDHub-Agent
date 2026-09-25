import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateJobs,validateJobsRequest,validateJobsSave} from '../src/server/jobs/bridge.ts';

const job={id:'taplink_prepare',name:'TapLink 准备',group:'材料',description:'货盘发布后准备链接。',manual:'workflow',manualEndpoint:'/api/workflow',lastRunAt:1789318884,enabled:false,at:'07:20',schedulable:true,cadence:'daily',weekday:null};
const agent={...job,id:'agent_reply',name:'Agent 回复',group:'会话',manual:'job-control',manualEndpoint:'/api/jobs',lastRunAt:null,at:'15:00'};
const scheduler={running:false,stopping:false,pid:null,startedAt:null,phase:null,cycle:null,checkedAt:null,lastSuccess:{},lastAttempt:{},nextDue:{},error:null};
const payload={market:'it',version:'jobs-v3',jobs:[job,agent],schedulerReady:true,scheduler};

test('a jobs state is accepted and keeps the manual endpoint and schedule intent',()=>{
 const v=validateJobs(payload,'it');
 assert.equal(v.jobs[0].manualEndpoint,'/api/workflow');
 assert.equal(v.jobs[0].enabled,false);
 assert.equal(v.jobs[0].at,'07:20');
 assert.equal(v.jobs[1].manualEndpoint,'/api/jobs');
 assert.equal(v.schedulerReady,true);
 assert.equal(v.scheduler.running,false);
});

test('every displayed job points to a real bounded control endpoint',()=>{
 const v=validateJobs(payload,'it');
 assert.equal(v.jobs[1].manual,'job-control');
 assert.equal(v.jobs[1].manualEndpoint,'/api/jobs');
 assert.equal(v.jobs[1].lastRunAt,null);
});

test('bad times, bad ids and duplicate jobs are rejected',()=>{
 for(const bad of [{...payload,jobs:[{...job,at:'25:00'}]},
                   {...payload,jobs:[{...job,at:'3:00'}]},
                   {...payload,jobs:[{...job,id:'Bad Id'}]},
                   {...payload,jobs:[{...job,enabled:'yes'}]},
                   {...payload,jobs:[{...job,schedulable:'yes'}]},
                   {...payload,jobs:[{...job,cadence:'monthly'}]},
                   {...payload,jobs:[{...job,weekday:7}]},
                   {...payload,jobs:[{...job,lastRunAt:'yesterday'}]},
                   {...payload,jobs:[]},
                   {...payload,jobs:[job,job]},
                   {...payload,version:''}]){
  assert.throws(()=>validateJobs(bad,'it'),/invalid_jobs/);
 }
});

test('a save request carries only the fields it means to change',()=>{
 assert.deepEqual(validateJobsSave({action:'save',jobs:{agent_reply:{enabled:true}}}),
  {jobs:{agent_reply:{enabled:true}}});
 assert.deepEqual(validateJobsSave({action:'save',jobs:{taplink_prepare:{at:'07:30'}}}),
  {jobs:{taplink_prepare:{at:'07:30'}}});
 assert.deepEqual(validateJobsSave({action:'save',jobs:{taplink_prepare:{at:null}}}),
  {jobs:{taplink_prepare:{at:null}}});
 assert.deepEqual(validateJobsSave({action:'save',jobs:{taplink_clean:{weekday:0}}}),
  {jobs:{taplink_clean:{weekday:0}}});
 assert.deepEqual(validateJobsRequest({action:'start_scheduler',market:'it'}),{action:'start_scheduler',market:'it'});
 assert.deepEqual(validateJobsRequest({action:'stop_scheduler',market:'it'}),{action:'stop_scheduler',market:'it'});
 for(const bad of [{action:'save'},{action:'run',jobs:{catalog_collect:{enabled:true}}},
                   {action:'save',jobs:{catalog_collect:{}}},
                   {action:'save',jobs:{catalog_collect:{at:'99:99'}}},
                   {action:'save',jobs:{catalog_collect:{enabled:1}}},
                   {action:'save',jobs:{catalog_collect:{weekday:7}}},
                   {action:'start_scheduler',extra:true},
                   {action:'save',jobs:{'bad id':{enabled:true}}},
                   {action:'save',jobs:{}},
                   null]){
  assert.throws(()=>validateJobsSave(bad),/invalid_jobs_request/);
 }
});

test('discovery exposes a fixed day interval instead of an editable weekly clock',()=>{
 const discovery={...job,id:'full_catalog_update',schedulable:false,at:null,cadence:'interval',intervalDays:15};
 const value=validateJobs({...payload,jobs:[discovery]},'it');
 assert.equal(value.jobs[0].intervalDays,15);
 assert.equal(value.jobs[0].schedulable,false);
 assert.equal(value.jobs[0].at,null);
 for(const intervalDays of [undefined,0,-1,1.5,'15']){
  assert.throws(()=>validateJobs({...payload,jobs:[{...discovery,intervalDays}]},'it'),/invalid_jobs/);
 }
});
