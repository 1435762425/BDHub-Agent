import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateIdentityState,validateIdentityRequest,validateIdentityConfig} from '../src/server/kalodata-identity/bridge.ts';

const config={version:'kalodata-identity-v1',activationCode:'CARD-1234',canaryPid:'1729480002729777701'};
const probe={verdict:'quota_exhausted',identityOk:true,detail:'kalodata_daily_quota_exhausted',pid:'1729480002729777701',rows:null,proxyConfigured:false,checkedAt:1789400000,elapsedSeconds:0.68};
const payload={config,grabber:'/Users/x/kalodatagrab/乘丰 对标查找',loginDir:'/x/Kalodata登录器独立版',pythonReady:true,
 cookie:{present:true,updatedAt:1789397531.85,bytes:1472},proxy:{configured:false,updatedAt:null},
 lastProbe:probe,probePid:'1729480002729777701',login:null,probeEndpoint:'kalodata',probeHint:'提示'};

test('an identity state is accepted and keeps the probe verdict',()=>{
 const v=validateIdentityState(payload);
 assert.equal(v.lastProbe.verdict,'quota_exhausted');
 assert.equal(v.lastProbe.identityOk,true);
 assert.equal(v.cookie.bytes,1472);
 assert.equal(v.proxy.configured,false);
});

test('a state that has never been probed or logged in is still valid',()=>{
 const v=validateIdentityState({...payload,lastProbe:null,login:null});
 assert.equal(v.lastProbe,null);
 assert.equal(v.login,null);
});

test('an open login window is reported with its mode',()=>{
 const v=validateIdentityState({...payload,login:{mode:'activate',pid:1234,startedAt:1,running:true,log:'/tmp/x'}});
 assert.equal(v.login.mode,'activate');
 assert.equal(v.login.running,true);
});

test('an unknown verdict or a malformed account state is rejected',()=>{
 for(const bad of [{...payload,lastProbe:{...probe,verdict:'maybe'}},
                   {...payload,lastProbe:{...probe,identityOk:'yes'}},
                   {...payload,cookie:{present:'yes'}},
                   {...payload,cookie:null},
                   {...payload,proxy:null},
                   {...payload,login:{mode:'activate'}},
                   {...payload,config:{...config,version:''}}]){
  assert.throws(()=>validateIdentityState(bad),/invalid_kalodata_identity/);
 }
});

test('a card with control whitespace and a short pid are refused before reaching the grabber',()=>{
 assert.throws(()=>validateIdentityConfig({...config,activationCode:'a\nb'}),/invalid_kalodata_identity/);
 assert.throws(()=>validateIdentityConfig({...config,activationCode:'x'.repeat(201)}),/invalid_kalodata_identity/);
 assert.throws(()=>validateIdentityConfig({...config,canaryPid:'123'}),/invalid_kalodata_identity/);
 assert.equal(validateIdentityConfig({...config,canaryPid:''}).canaryPid,'');
});

test('save needs a config while the run actions do not',()=>{
 assert.deepEqual(validateIdentityRequest({action:'save',config}),{action:'save',config});
 for(const action of ['probe','activate','refresh']){
  assert.deepEqual(validateIdentityRequest({action}),{action});
 }
 for(const bad of [{action:'delete'},{action:'save'},{action:'save',config:{...config,canaryPid:'1'}},null]){
  assert.throws(()=>validateIdentityRequest(bad),/invalid_kalodata_identity_request/);
 }
});
