import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createAccountsGet,validateAccountMutation} from '../src/server/market-accounts/bridge.ts';
const url='http://127.0.0.1:5198/api/market-accounts';
test('account observation rejects remote origins and mutation-shaped requests before reading',async()=>{
 let calls=0;const get=createAccountsGet(async()=>{calls++;return {executionEnabled:false,markets:[],realSends:0};});
 assert.equal((await get(new Request(url,{headers:{host:'127.0.0.1:5198',origin:'https://other.test'}}))).status,403);
 assert.equal((await get(new Request(url+'?refresh=1',{headers:{host:'127.0.0.1:5198'}}))).status,400);
 assert.equal((await get(new Request(url,{method:'POST',headers:{host:'127.0.0.1:5198'}}))).status,405);
 assert.equal(calls,0);
 const r=await get(new Request(url,{headers:{host:'127.0.0.1:5198'}}));assert.equal(r.status,200);assert.equal(r.headers.get('cache-control'),'no-store');assert.equal((await r.json()).executionEnabled,false);
});
test('backend failure has a bounded public error',async()=>{
 const get=createAccountsGet(async()=>{throw Error('private backend detail');});
 const r=await get(new Request(url,{headers:{host:'127.0.0.1:5198'}}));assert.equal(r.status,503);assert.deepEqual(await r.json(),{error:'account_status_unavailable'});
});
test('account mutations are fixed to market assignments and revisioned enable controls',()=>{assert.deepEqual(validateAccountMutation({action:'refresh',market:'it',account:'acc6',requestId:'refresh-0001'}),{action:'refresh',market:'it',account:'acc6',requestId:'refresh-0001'});assert.deepEqual(validateAccountMutation({action:'set_enabled',market:'uk',account:'acc4',requestId:'setting-0001',expectedRevision:2,enabled:false}),{action:'set_enabled',market:'uk',account:'acc4',requestId:'setting-0001',expectedRevision:2,enabled:false});assert.throws(()=>validateAccountMutation({action:'relogin',market:'it',account:'acc7',requestId:'relogin-0001'}),/invalid_account_request/);assert.throws(()=>validateAccountMutation({action:'refresh',market:'it',account:'acc6',requestId:'refresh-0001',path:'/tmp/secret'}),/invalid_account_request/);});
