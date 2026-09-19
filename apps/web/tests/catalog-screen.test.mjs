import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateState,validateScreenRequest} from '../src/server/catalog-screen/bridge.ts';

const config={version:'catalog-screen-v1',minSales:300,minRating:4,minCommissionGapPoints:2,allowUnrated:true};
const fingerprint='bb83b0006c521c542776e834fc1d4c909743304fbe5fbef1bb41604b5100bff0';
const bounds={minSales:[0,1000000],minRating:[0,5],minCommissionGapPoints:[0,100]};
const record={runId:'screen-94f7e7bbf74a021969542164',sourceRun:'it-global-20260914',fingerprint,state:'recorded',counts:{eligible:2291,rejected:7709},reasons:{sales_below_min:7484,rating_below_min:1390},updatedAt:1789318884.1};
const payload={config,fingerprint,defaults:config,bounds,configPath:'config/catalog-screen.json',
 collection:{runId:'it-global-20260914',state:'completed',products:10000,reportedTotal:10000,observedAt:1789318884.1},
 screen:record,
 funnel:{...record,config,collected:10000,eligible:2291,rejected:7709,selectedEligible:1692,unselectedEligible:599,unknownSelectedFlag:0,unrated:421,unratedEligible:0}};

test('a screening state is accepted and keeps the funnel and reason numbers',()=>{
 const v=validateState(payload);
 assert.equal(v.funnel.eligible,2291);
 assert.equal(v.funnel.unselectedEligible,599);
 assert.equal(v.funnel.reasons.sales_below_min,7484);
 assert.equal(v.collection.products,10000);
 assert.deepEqual(v.bounds.minSales,[0,1000000]);
});

test('a collection that has not been screened yet is still a valid state',()=>{
 const v=validateState({...payload,screen:null,funnel:null,collection:null});
 assert.equal(v.screen,null);
 assert.equal(v.funnel,null);
 assert.equal(v.collection,null);
});

test('out-of-range thresholds and malformed bridge output are rejected',()=>{
 for(const bad of [{...payload,config:{...config,minSales:-1}},
                   {...payload,config:{...config,minSales:300.5}},
                   {...payload,config:{...config,minRating:5.5}},
                   {...payload,config:{...config,minCommissionGapPoints:101}},
                   {...payload,config:{...config,allowUnrated:'yes'}},
                   {...payload,config:{...config,version:''}},
                   {...payload,fingerprint:'not-a-hash'},
                   {...payload,funnel:{...payload.funnel,eligible:-1}},
                   {...payload,funnel:{...payload.funnel,reasons:{sales_below_min:'many'}}},
                   {...payload,bounds:{minSales:[0]}}]){
  assert.throws(()=>validateState(bad),/invalid_catalog_screen/);
 }
});

test('preview and save requests are validated before reaching the bridge',()=>{
 assert.deepEqual(validateScreenRequest({action:'save',config}),{action:'save',config});
 assert.equal(validateScreenRequest({action:'preview',config}).action,'preview');
 for(const bad of [{action:'delete',config},{action:'save'},{action:'save',config:{...config,minRating:9}},null]){
  assert.throws(()=>validateScreenRequest(bad),/invalid_catalog_screen_request/);
 }
});

test('a preview answer is accepted alongside the thresholds in force',()=>{
 const preview={collected:10000,eligible:3053,rejected:6947,unselectedEligible:1036,unratedEligible:2,addedVersusActive:762,removedVersusActive:0,reasons:{sales_below_min:6607},config:{...config,minSales:200},fingerprint:'a'.repeat(64),activeFingerprint:fingerprint};
 const v=validateState({...payload,previewConfig:{...config,minSales:200},preview});
 assert.equal(v.preview.addedVersusActive,762);
 assert.equal(v.previewConfig.minSales,200);
});
