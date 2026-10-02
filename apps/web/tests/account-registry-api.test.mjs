import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRegistryHandlers,validateRegistryMutation} from '../src/server/account-registry/bridge.ts';
const url='http://127.0.0.1:5198/api/account-registry';
const local={host:'127.0.0.1:5198'};
const post=(body,extra={})=>new Request(url,{method:'POST',headers:{...local,origin:'http://127.0.0.1:5198','content-type':'application/json',...extra},body:JSON.stringify(body)});
const add={action:'add',account:'acc12',market:'uk',partnerId:'8800',username:'ops@example.com',password:'PRIVATE_PW'};
test('registry requests are local-only and validated before the CLI runs',async()=>{
 const calls=[];const {GET,POST}=createRegistryHandlers(async(action,input)=>{calls.push([action,input]);return {accounts:[],secretsReturned:0};});
 assert.equal((await GET(new Request(url,{headers:{host:'127.0.0.1:5198',origin:'https://other.test'}}))).status,403);
 assert.equal((await POST(post(add,{origin:'https://other.test'}))).status,403);
 assert.equal((await POST(post({...add,profileDir:'/tmp/x'}))).status,400);
 assert.equal((await POST(post({...add,account:'acc6;rm'}))).status,400);
 assert.equal(calls.length,0);
 assert.equal((await POST(post(add))).status,200);
 assert.deepEqual(calls,[['add',{account:'acc12',market:'uk',partnerId:'8800',username:'ops@example.com',password:'PRIVATE_PW'}]]);
});
test('registry errors map to bounded public codes',async()=>{
 const {POST}=createRegistryHandlers(async()=>{throw Error('account_exists');});
 const response=await POST(post(add));assert.equal(response.status,409);assert.deepEqual(await response.json(),{error:'account_exists'});
 const hidden=createRegistryHandlers(async()=>{throw Error('PRIVATE detail');}).POST;
 assert.deepEqual(await (await hidden(post(add))).json(),{error:'account_registry_unavailable'});
});
test('credential updates carry only the fields given',()=>{
 assert.deepEqual(validateRegistryMutation({action:'update',account:'acc6',password:'NEW'}),{action:'update',account:'acc6',password:'NEW'});
 assert.throws(()=>validateRegistryMutation({action:'update',account:'acc6'}),/invalid_registry_request/);
 assert.throws(()=>validateRegistryMutation({action:'update',account:'acc6',password:' padded'}),/invalid_registry_request/);
});
