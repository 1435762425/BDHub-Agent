import test from 'node:test';
import assert from 'node:assert/strict';
import {operatorQueueSummary,nextWindowText,operationsActivity} from '../src/features/operations/presentation.ts';

const NOW=Date.parse('2026-10-24T07:00:00Z');
const base={market:'br',setting:{automaticOperationsEnabled:true,continuousSendEnabled:true},workflow:{state:'completed'},stages:[{id:'catalog',label:'货盘',state:'completed'},{id:'continuous_send',state:'waiting_window'},{id:'agent_reply',state:'outside_reply_window'}],continuousSend:{control:{window:['16:30','24:00'],automaticEnabled:false,runRequested:false,stopRequested:false}},agent:{enabled:true,replyWindow:['00:30','08:00']}};
test('the human queue keeps missing counts distinct from a real empty queue',()=>{
 for(const queue of [undefined,null])assert.equal(operatorQueueSummary(queue).value,null);
 assert.deepEqual(operatorQueueSummary({human:0,technical:4}),{value:0,text:'暂无人工会话待办'});
 assert.equal(operatorQueueSummary({human:36,technical:1}).value,36);
});
test('next windows use Beijing configuration and never guess absent or invalid clocks',()=>{
 assert.equal(nextWindowText(['16:30','24:00'],NOW),'今天 16:30（北京时间）');
 assert.equal(nextWindowText(['00:30','08:00'],NOW),'明天 00:30（北京时间）');
 assert.equal(nextWindowText(['16:30','24:00'],Date.parse('2026-10-24T08:30:05Z')),'窗口已到，等待下一次调度检查');
 for(const value of [undefined,null,[],['25:00','24:00'],['16:30','invalid'],['16:30','08:00']])assert.equal(nextWindowText(value,NOW),null);
});
test('normal sender and reply windows explain continuation without becoming human failures',()=>{
 const rows=operationsActivity(base,NOW);
 assert.match(rows[1].detail,/今天 16:30.*原授权自动继续/);assert.equal(rows[1].attention,false);
 assert.match(rows[2].detail,/明天 00:30/);assert.equal(rows[2].attention,false);
 const missing=operationsActivity({...base,continuousSend:{control:{...base.continuousSend.control,window:undefined}}},NOW);assert.match(missing[1].detail,/时间尚未核实/);
});
test('an existing sender page authorization continues independently of the workflow toggle',()=>{
 for(const allowed of [{runRequested:true},{automaticEnabled:true}]){
  const source={...base,setting:{...base.setting,continuousSendEnabled:false},continuousSend:{control:{...base.continuousSend.control,...allowed}}};
  assert.match(operationsActivity(source,NOW)[1].detail,/今天 16:30.*原授权自动继续/);
 }
});
test('sender stop and unreadable persistent controls cannot promise automatic continuation',()=>{
 for(const control of [{...base.continuousSend.control,runRequested:true,automaticEnabled:true,stopRequested:true},undefined,{window:['16:30','24:00']}]){
  const row=operationsActivity({...base,continuousSend:{control}},NOW)[1];
  assert.doesNotMatch(row.detail,/今天|明天|自动继续/);
  assert.match(row.detail,control?.stopRequested?/授权未开启/:/授权尚未核实/);
 }
});
test('paused plans, stale samples and current failures cannot be presented as normal scheduled continuation',()=>{
 const stage=(state,runtime)=>base.stages.map(row=>row.id==='agent_reply'?{...row,state,runtime}:row);
 const paused=operationsActivity({...base,stages:stage('plan_paused',{fresh:true,enabled:false})},NOW)[2];assert.equal(paused.state,'计划已暂停');assert.doesNotMatch(paused.detail,/自动继续/);
 const stale=operationsActivity({...base,stages:stage('outside_reply_window',{fresh:false})},NOW)[2];assert.equal(stale.state,'状态待核实');assert.equal(stale.attention,true);assert.doesNotMatch(stale.detail,/明天|自动继续/);
 const failed=operationsActivity({...base,workflow:{state:'failed'},stages:[{id:'catalog',label:'货盘',state:'queued'},...base.stages.slice(1)]},NOW)[0];assert.equal(failed.state,'运行失败');assert.equal(failed.attention,true);
 const off=operationsActivity({...base,agent:{...base.agent,enabled:false}},NOW)[2];assert.doesNotMatch(off.detail,/明天|自动继续/);
});
