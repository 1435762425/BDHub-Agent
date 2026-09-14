import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateLinkStatus} from '../src/server/global-source/bridge.ts';
const summary={runId:'catalog-prepare-1',total:2,states:{ready:1,missing:1},retiredCount:0,verifiedLinkCount:1,verifiedPidCount:1,reusePidCount:0,pendingCount:1,incompleteCount:0,errors:[],blockers:[]};
const item={pid:'1729480061238089885',campaignId:'7685262119046498070',state:'ready',error:null,blocker:null,creatorPercent:'13',publicPercent:'12',totalPercent:'14',title:'Quaderno',intentId:null,updated:1};
const fixture={available:true,executionAllowed:false,summary,items:[item]};
test('link status cannot grant execution and keeps links separate from covered pids',()=>{
 const v=validateLinkStatus(fixture);
 assert.equal(v.summary.verifiedLinkCount,1);
 assert.equal(v.summary.verifiedPidCount,1);
 assert.equal(v.items[0].creatorPercent,'13');
 assert.throws(()=>validateLinkStatus({...fixture,executionAllowed:true}),/invalid_link_status/);
});
test('malformed or over-wide link status is rejected',()=>{
 for(const bad of [{...fixture,summary:{...summary,verifiedLinkCount:-1}},
                   {...fixture,summary:{...summary,errors:null}},
                   {...fixture,items:[item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item,item]},
                   {...fixture,items:[{...item,pid:'not-a-pid'}]},
                   {...fixture,items:[{...item,title:3}]}]){
  assert.throws(()=>validateLinkStatus(bad),/invalid_link_status/);
 }
});
test('an unavailable ledger is reported without invented numbers',()=>{
 const v=validateLinkStatus({available:false,executionAllowed:false,reason:'catalog_prepare_not_started'});
 assert.equal(v.available,false);assert.equal(v.summary,undefined);
});
