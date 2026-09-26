import {test} from 'node:test';import assert from 'node:assert/strict';
import {validateOpsAlerts} from '../src/server/ops-alerts/bridge.ts';
import {GET} from '../src/app/api/ops-alerts/route.ts';
const alert={id:'my-inbox-error',level:'warning',market:'my',title:'MY 收信失败',detail:'taplink_remote_read_failed（auth 阶段）',since:1790217762.5,href:'/my/ops/accounts'};
const payload={schemaVersion:'bdhub.ops-alerts.v1',checkedAt:1790240000,alerts:[alert,{...alert,id:'offsite-missing',market:null,since:null,href:null}],readOnly:true,platformWrites:0};
test('alert payload keeps levels, times and in-app links',()=>{const value=validateOpsAlerts(payload);assert.equal(value.alerts.length,2);assert.equal(value.alerts[0].href,'/my/ops/accounts');assert.equal(value.alerts[1].market,null);});
test('alert payload refuses writes, unknown levels and links that leave the app',()=>{
 assert.throws(()=>validateOpsAlerts({...payload,platformWrites:1}),/invalid_ops_alerts/);
 for(const change of [{level:'fatal'},{href:'https://example.com/x'},{href:'//example.com'},{href:'/my/../x'},{detail:'x'.repeat(401)},{since:-1}])
  assert.throws(()=>validateOpsAlerts({...payload,alerts:[{...alert,...change}]}),/invalid_ops_alerts/,JSON.stringify(change));
});
test('alert route only answers plain local reads',async()=>{
 assert.equal((await GET(new Request('http://127.0.0.1:5198/api/ops-alerts?market=it',{headers:{host:'127.0.0.1:5198'}}))).status,400);
 assert.equal((await GET(new Request('http://127.0.0.1:5198/api/ops-alerts',{headers:{host:'evil.example'}}))).status,403);
});
test('runtime evidence is validated and absent evidence reads as unknown',()=>{
 const evidence={head:'a'.repeat(40),processes:[{role:'scheduler',pid:12,sha:'b'.repeat(40),startedAt:1,current:false}],
  scheduler:{running:true,checkedAt:2},modelService:{paused:true,nextAt:3,lastError:'provider_timeout'},
  restoreDrill:{backup:'20260926T061807Z-pre',state:'restorable',finishedAt:4,blockers:[]}};
 const value=validateOpsAlerts({...payload,evidence});
 assert.equal(value.evidence.processes[0].current,false);assert.equal(value.evidence.restoreDrill.state,'restorable');
 assert.equal(validateOpsAlerts(payload).evidence,null);
 assert.throws(()=>validateOpsAlerts({...payload,evidence:{...evidence,head:'not a sha'}}),/invalid_ops_alerts/);
 assert.throws(()=>validateOpsAlerts({...payload,evidence:{...evidence,processes:[{role:'x',pid:-1,sha:null,startedAt:null,current:true}]}}),/invalid_ops_alerts/);
});
