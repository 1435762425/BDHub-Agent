import test from "node:test";
import assert from "node:assert/strict";
import {BUSINESS_PHASES,DEMO_COUNTS,FLOW_STAGES,PID_REFRESH_CLOCKS,PID_SNAPSHOT,REFRESH_RULES,SCENARIOS,SIMPLE_POOL,TAPLINK_PERFORMANCE,TAPLINK_VALIDITY_CHECKS,evaluateScenario,poolReconciles,simpleScenario} from "../src/features/demo/pid-send-pool-demo.ts";

const scenario=key=>SCENARIOS.find(item=>item.key===key);

test("the demo follows the confirmed PID to strict-pool order",()=>{
 assert.deepEqual(FLOW_STAGES.map(item=>item.key),["collect","screen","offer","link","leads","identity","position","pool"]);
 assert.match(FLOW_STAGES[3].rule,/历史卡忽略/);
 assert.match(FLOW_STAGES[4].rule,/7 天/);
});

test("the default experience reduces the flow to three business questions",()=>{
 assert.deepEqual(BUSINESS_PHASES.map(item=>item.key),["product","creator","send"]);
 assert.deepEqual(Object.keys(SIMPLE_POOL),["sendable","waiting","unavailable","total"]);
 assert.equal(SIMPLE_POOL.sendable+SIMPLE_POOL.waiting+SIMPLE_POOL.unavailable,SIMPLE_POOL.total);
});

test("the PID snapshot reconciles source, link and historical cleanup counts",()=>{
 const full=PID_SNAPSHOT.fullManaged;
 const campaign=PID_SNAPSHOT.campaign;
 assert.equal(full.currentEligible+full.currentRejected,full.collected);
 assert.equal(Object.values(full.links).reduce((sum,value)=>sum+value,0),full.selectedPool);
 assert.equal(campaign.chosen+campaign.held,campaign.uniquePids);
 assert.equal(PID_SNAPSHOT.historicalCleanup.valid+PID_SNAPSHOT.historicalCleanup.invalid,
              PID_SNAPSHOT.historicalCleanup.scanned);
});

test("the fake pool counts reconcile exactly",()=>{
 assert.equal(poolReconciles(),true);
 assert.equal(Object.values(DEMO_COUNTS.pool).reduce((sum,value)=>sum+value,0),DEMO_COUNTS.positions);
});

test("a product without a rating can enter when sales and commission qualify",()=>{
 const result=evaluateScenario(scenario("unrated"));
 assert.equal(result.layer,"严格发送池 · Ready");
 assert.equal(result.gates[0].state,"pass");
});

test("sales below 300 still fail even when no rating is available",()=>{
 const value={...scenario("unrated"),sales:299};
 const result=evaluateScenario(value);
 assert.equal(result.gates[0].state,"stop");
 assert.equal(result.layer,"商品失效 / 不合格");
});

test("a legacy-only product waits for one standard card and ignores the old one",()=>{
 const result=evaluateScenario(scenario("legacy_only"));
 assert.equal(result.layer,"待补标准链接");
 assert.match(result.summary,/历史卡不参与发送/);
 assert.equal(result.gates.find(item=>item.key==="link")?.state,"wait");
});

test("the simplified scenario answers only product creator and send readiness",()=>{
 const clean=simpleScenario(scenario("clean"));
 const legacyOnly=simpleScenario(scenario("legacy_only"));
 assert.deepEqual([clean.product,clean.creator,clean.send],[true,true,true]);
 assert.deepEqual([legacyOnly.product,legacyOnly.creator,legacyOnly.send],[false,true,false]);
});

test("an invalid product and an unresolved reply leave ready for different reasons",()=>{
 const invalid=evaluateScenario(scenario("product_invalid"));
 const reply=evaluateScenario(scenario("reply_open"));
 assert.equal(invalid.layer,"商品失效 / 不合格");
 assert.equal(reply.layer,"等待回复处理");
 assert.equal(reply.gates.find(item=>item.key==="relation")?.state,"wait");
});

test("campaign expiry is dynamic and an open reply freezes the creator rather than the PID",()=>{
 const expired=simpleScenario(scenario("product_invalid"));
 const reply=simpleScenario(scenario("reply_open"));
 assert.equal(scenario("product_invalid").joinedCampaignDays,72);
 assert.equal(scenario("product_invalid").campaignDays,44);
 assert.deepEqual([expired.product,expired.send],[false,false]);
 assert.deepEqual([reply.product,reply.creator,reply.send],[true,true,false]);
});

test("TapLink refresh has exactly one rule for each product source",()=>{
 assert.deepEqual(PID_REFRESH_CLOCKS.map(item=>item.key),["campaign","selected"]);
 assert.equal(PID_REFRESH_CLOCKS.find(item=>item.key==="campaign")?.cadence,"每日");
 assert.equal(PID_REFRESH_CLOCKS.find(item=>item.key==="selected")?.cadence,"每周");
 assert.doesNotMatch(PID_REFRESH_CLOCKS.flatMap(item=>item.items).join(" "),/fresh_card|发送前/);
 assert.match(REFRESH_RULES.find(item=>item.object==="Campaign 商品 \+ TapLink")?.effect??"",/确认失效则清理/);
 assert.match(REFRESH_RULES.find(item=>item.object==="全托已选 TapLink")?.cycle??"",/每周/);
});

test("performance guidance explains why selected TapLinks refresh weekly",()=>{
 assert.deepEqual(TAPLINK_PERFORMANCE.map(item=>item.label),["单 PID 严格核验","1,908 张库存扫描"]);
 assert.equal(TAPLINK_PERFORMANCE[0].value,"约 2 秒");
 assert.match(PID_REFRESH_CLOCKS.find(item=>item.key==="selected")?.items.join(" ")??"",/失效则清理/);
});

test("TapLink validity means one standard rule and one canonical send card",()=>{
 assert.deepEqual(TAPLINK_VALIDITY_CHECKS.map(item=>item.label),["统一规则","统一名称","精确绑定","唯一发送卡","历史卡"]);
 assert.match(TAPLINK_VALIDITY_CHECKS.map(item=>item.detail).join(" "),/listId/);
 assert.match(TAPLINK_VALIDITY_CHECKS.map(item=>item.detail).join(" "),/commission-1-to-2-v1/);
});
