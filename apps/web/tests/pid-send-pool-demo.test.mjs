import test from "node:test";
import assert from "node:assert/strict";
import {DEMO_COUNTS,FLOW_STAGES,SCENARIOS,evaluateScenario,poolReconciles} from "../src/features/demo/pid-send-pool-demo.ts";

const scenario=key=>SCENARIOS.find(item=>item.key===key);

test("the demo follows the confirmed PID to strict-pool order",()=>{
 assert.deepEqual(FLOW_STAGES.map(item=>item.key),["collect","screen","offer","link","leads","identity","position","pool"]);
 assert.match(FLOW_STAGES[3].rule,/旧卡保留/);
 assert.match(FLOW_STAGES[4].rule,/7 天/);
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

test("a rate mismatch waits for a new card without deleting the old one",()=>{
 const result=evaluateScenario(scenario("rate_changed"));
 assert.equal(result.layer,"待重建");
 assert.match(result.summary,/旧卡继续保留/);
 assert.equal(result.gates.find(item=>item.key==="link")?.state,"wait");
});

test("an invalid product and an unresolved reply leave ready for different reasons",()=>{
 const invalid=evaluateScenario(scenario("product_invalid"));
 const reply=evaluateScenario(scenario("reply_open"));
 assert.equal(invalid.layer,"商品失效 / 不合格");
 assert.equal(reply.layer,"等待回复处理");
 assert.equal(reply.gates.find(item=>item.key==="relation")?.state,"wait");
});
