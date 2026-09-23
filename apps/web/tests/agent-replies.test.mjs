import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createAgentRepliesHandlers} from '../src/server/agent-replies/bridge.ts';

const base='http://127.0.0.1:5198/api/agent-replies?market=it';
const headers={host:'127.0.0.1:5198',origin:'http://127.0.0.1:5198','content-type':'application/json'};

test('agent settings read requires local origin and exact market',async()=>{
 let calls=0;const api=createAgentRepliesHandlers(async()=>{calls++;return {ok:true};});
 assert.equal((await api.GET(new Request(base,{headers:{...headers,origin:'https://other.test'}}))).status,403);
 assert.equal((await api.GET(new Request(base+'&market=br',{headers}))).status,400);
 assert.equal(calls,0);
});

test('simulation carries bounded history and cannot become a send command',async()=>{
 const calls=[];const api=createAgentRepliesHandlers(async(...args)=>{calls.push(args);return {decisionId:'agent-decision-'+'a'.repeat(24)};});
 const valid={action:'simulate',market:'it',history:[{direction:'inbound',text:'Certo'}]};
 assert.equal((await api.POST(new Request(base,{method:'POST',headers,body:JSON.stringify(valid)}))).status,200);
 assert.deepEqual(calls[0],['simulate','it',{history:valid.history},undefined]);
 assert.equal((await api.POST(new Request(base,{method:'POST',headers,body:JSON.stringify({...valid,send:true})}))).status,400);
 assert.equal(calls.length,1);
});

test('trace requires a valid decision id and is read only',async()=>{
 const calls=[];const api=createAgentRepliesHandlers(async(...args)=>{calls.push(args);return {ok:true};});
 assert.equal((await api.GET(new Request(base+'&decisionId=../../secret',{headers}))).status,400);
 assert.equal((await api.GET(new Request(base+'&decisionId=agent-decision-'+'a'.repeat(24),{headers}))).status,200);
 assert.equal(calls[0][0],'trace');
});

test('first real reply is a separate explicit page command',async()=>{
 const calls=[];const api=createAgentRepliesHandlers(async(...args)=>{calls.push(args);return {stage:'pilot_running'};});
 const command={action:'start-first',market:'it',requestId:'pilot-request-0001'};
 const result=await api.POST(new Request(base,{method:'POST',headers,body:JSON.stringify(command)}));
 assert.equal(result.status,200);
 assert.deepEqual(calls[0],['start-first','it',{requestId:command.requestId},undefined]);
 assert.equal((await api.POST(new Request(base,{method:'POST',headers,body:JSON.stringify({...command,market:'br'})}))).status,400);
});
