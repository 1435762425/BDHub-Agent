import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateCampaignJoin,validateCampaignJoinRequest,validateCampaignLinks,validateCampaignPanel} from '../src/server/campaign/bridge.ts';

const panel={available:true,market:'it',source:'campaign',snapshot:'catalog-1',offers:3622,
 counts:{eligible:925,ineligible:2697},reasons:{insufficient_commission_gap:2057},
 eligiblePids:420,multiCampaignPids:223,
 pool:{counts:{chosen:420,held:2248},withAlternatives:223,reconciled:true,
  sample:[{pid:'1'.repeat(19),campaignId:'9'.repeat(19),creatorPercent:'13',
   endAt:'2027-09-04T00:00:00+00:00',alternatives:[{campaignId:'8'.repeat(19),creatorPercent:'11',endAt:null}]}]},
 recorded:{runId:'campaign-screen-1',snapshot:'catalog-1',updated:1}};

const join={available:true,market:'it',jobId:'job-1',state:'needs_verification',error:'',account:'acc9',joinedCount:2,
 counts:{joined:2,result_unknown:1},unresolved:['1'.repeat(19)],
 items:[{campaign_id:'1'.repeat(19),name:'活动',state:'result_unknown',reason:'result_unknown',write_attempted:1,joined_campaign_id:'9'.repeat(19)}],
 platformWrites:1};

test('the campaign pool summary carries offers, pids and the pool identity flag',()=>{
 const v=validateCampaignPanel(panel,'it');
 assert.equal(v.offers,3622);
 assert.equal(v.eligiblePids,420);
 assert.equal(v.poolCounts.chosen,420);
 assert.equal(v.poolReconciled,true);
 assert.equal(v.sample[0].creatorPercent,'13');
 assert.equal(v.sample[0].alternatives[0].campaignId,'8'.repeat(19));
});

test('a workspace without a campaign snapshot is reported, not shown as zeroes',()=>{
 const v=validateCampaignPanel({available:false,market:'it',reason:'snapshot_missing'},'it');
 assert.equal(v.available,false);
 assert.equal(v.reason,'snapshot_missing');
});

test('a join ledger reports what is written but unsettled',()=>{
 const v=validateCampaignJoin(join,'it');
 assert.equal(v.state,'needs_verification');
 assert.equal(v.joinedCount,2);
 assert.deepEqual(v.unresolved,['1'.repeat(19)]);
 assert.equal(v.items[0].writeAttempted,true);
 assert.equal(v.items[0].joinedCampaignId,'9'.repeat(19));
 assert.equal(v.platformWrites,1);
});

test('an unknown join state before anything ran is unavailable',()=>{
 const v=validateCampaignJoin({available:false,market:'it',reason:'campaign_join_not_started'},'it');
 assert.equal(v.available,false);
});

test('a join request must be confirmed and carry a valid email and ids',()=>{
 assert.deepEqual(validateCampaignJoinRequest({action:'preview',market:'it'}),{action:'preview',market:'it'});
 assert.deepEqual(validateCampaignJoinRequest({action:'verify',market:'it'}),{action:'verify',market:'it'});
 const ids=['1'.repeat(19),'2'.repeat(19)];
 assert.deepEqual(validateCampaignJoinRequest({action:'apply',market:'it',campaignIds:ids,email:'a@b.com',confirm:true}),
  {action:'apply',market:'it',campaignIds:ids,email:'a@b.com',confirm:true});
 for(const bad of [{action:'apply',market:'it',campaignIds:ids,email:'a@b.com'},
                   {action:'apply',market:'it',campaignIds:ids,email:'a@b.com',confirm:false},
                   {action:'apply',market:'it',campaignIds:['111'],email:'a@b.com',confirm:true},
                   {action:'apply',market:'it',campaignIds:ids,email:'not-an-email',confirm:true},
                   {action:'apply',market:'it',campaignIds:[ids[0],ids[0]],email:'a@b.com',confirm:true},
                   {action:'preview',market:'it',campaignIds:ids},{action:'stop'},null]){
  assert.throws(()=>validateCampaignJoinRequest(bad));
 }
});

test('campaign link preparation reports the pool targets and never claims a write',()=>{
 const state={available:true,route:'campaign',runId:'catalog-prepare-1',
  scope:{market:'it',route:'campaign',sourceRun:'campaign-screen-1'},
  selection:{poolRun:'campaign-screen-1',snapshot:'catalog-a2d1',targets:419,
   skipped:{excluded:1,plan_missing:0,commission_invalid:0}},
  summary:{total:419,states:{missing:395,reuse:24},verifiedPidCount:0,reusePidCount:24,pendingCount:395}};
 const v=validateCampaignLinks(state,'it');
 assert.equal(v.route,undefined);            // route 只用于校验，不放进页面状态
 assert.equal(v.targets,419);
 assert.equal(v.excluded,1);
 assert.equal(v.reusePids,24);
 assert.equal(v.states.missing,395);
 assert.equal(v.platformWrites,0);           // 只查不建：写入次数只能是 0
 assert.equal(v.executionAllowed,false);
 // 没开始过是"还没有"，不是错误。
 assert.deepEqual(validateCampaignLinks({available:false,reason:'catalog_prepare_not_started'},'it'),
  {available:false,market:'it',reason:'catalog_prepare_not_started'});
 // 全托的 run 不能被当成非全托的状态渲染出来。
 assert.throws(()=>validateCampaignLinks({...state,route:'selected'},'it'),/invalid_campaign_links/);
 assert.throws(()=>validateCampaignLinks({...state,summary:{...state.summary,states:{missing:-1}}},'it'),
  /invalid_missing/);
});

test('current status payloads are not mistaken for failures',async()=>{
 // 回归（同一个坑的两半）：
 //  ① campaign-join.py 成功时也带 error:""（空串），桥接曾把它当失败；
 //  ② 改成"非空即失败"之后，status 里那个 **job 的历史错误**又把它当成了传输失败——
 //     于是"只要这个 job 存过一次错误，状态接口就再也读不出来"。
 // 正确判据是"只有 error 一个键"的裸信封（各 CLI 的失败输出就是 json.dumps({"error": code})）。
 const {readCampaignJoin}=await import('../src/server/campaign/bridge.ts');
 const v=await readCampaignJoin('it');          // 必须 resolve，不能 reject
 assert.equal(v.available,true);
 assert.equal(typeof v.error,'string');     // 历史错误是**数据**，照实显示
 assert.ok((v.items??[]).length>0);
});

test('a bare error envelope from the CLI is still surfaced as a failure',async()=>{
 // 另一半必须保住：真正的失败（裸信封）仍要 reject 出它自己的错误码，而不是被当成成功。
 // 邮箱非法会在任何写入之前就被 join_payload 拒绝，所以这一条不会碰平台。
 const {applyCampaignJoin}=await import('../src/server/campaign/bridge.ts');
 await assert.rejects(()=>applyCampaignJoin('it',['1'.repeat(19)],'not-an-email'),/campaign_join_email_invalid/);
});

test('one-click join takes no campaign list and still demands confirmation',()=>{
 const call={action:'joinAll',market:'it',email:'a@b.com',confirm:true};
 assert.deepEqual(validateCampaignJoinRequest(call),call);
 // 目标集合必须由服务端重新预览得出：页面多带一个活动名单就是坏请求，
 // 否则页面可以拿一份过期名单去写平台。
 for(const bad of [{action:'joinAll',market:'it',email:'a@b.com',confirm:true,campaignIds:['1'.repeat(19)]},
                   {action:'joinAll',market:'it',email:'a@b.com'},
                   {action:'joinAll',market:'it',email:'not-an-email',confirm:true},
                   {action:'joinAll',market:'it',confirm:true}]){
  assert.throws(()=>validateCampaignJoinRequest(bad),/invalid_campaign_join_request|campaign_join_confirmation_required/);
 }
});
