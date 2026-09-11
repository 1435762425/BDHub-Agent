import test from "node:test";
import assert from "node:assert/strict";
import {
  initialState,
  demoReducer,
  activeCaseFor,
  creatorFor,
  productFor,
  relationFor,
  MARKETS,
} from "../src/features/bdhub/model.ts";

const reduce = (state, ...actions) => actions.reduce(demoReducer, state);
const caseById = (state, id) => {
  const item = state.cases.find((candidate) => candidate.id === id);
  assert.ok(item, `missing fixture case ${id}`);
  return item;
};
const outboundCount = (state) => state.cases.reduce(
  (count, item) => count + item.messages.filter((message) => message.direction === "team").length,
  0,
);
const activeCases = (state, creatorId, productId) => state.cases.filter(
  (item) => item.creatorId === creatorId && item.productId === productId && item.status !== "closed",
);
const completedCooperations = (state) => state.cases.filter(
  (item) => item.status === "closed" && item.adopted,
);

function camilaWithTwoCases() {
  return reduce(initialState(),
    {type: "activate_opportunity", id: "o4"},
    {type: "activate_opportunity", id: "o11"},
  );
}

function optedOutSofia() {
  const state = initialState();
  state.opportunities.push({
    id: "synthetic-sofia-new-product",
    creatorId: "sofia",
    productId: "phone-mx",
    source: "second",
    evidence: "合成测试：同关系的另一款商品机会",
    readiness: "ready",
  });
  return demoReducer(state, {
    type: "simulate_event", caseId: "case-sofia-watch-mx", event: "opt_out",
  });
}

test("同一机会重复进入与 first/second 同人同品命中只产生一个事项", () => {
  const seed = initialState();
  seed.opportunities.push({...seed.opportunities.find((item) => item.id === "o4"),
    id: "synthetic-o4-second", source: "second", readiness: "ready"});
  const beforeSends = outboundCount(seed);
  const state = reduce(seed,
    {type: "activate_opportunity", id: "o4"},
    {type: "activate_opportunity", id: "o4"},
    {type: "activate_opportunity", id: "synthetic-o4-second"},
  );
  assert.equal(activeCases(state, "camila", "watch-mx").length, 1);
  assert.equal(outboundCount(state), beforeSends, "准备机会不能隐式发送");
  const resumed = reduce(state,
    {type: "activate_opportunity", id: "o1"},
    {type: "activate_opportunity", id: "o9"},
  );
  assert.equal(activeCases(resumed, "sofia", "watch-mx").length, 1);
});

test("关系接管作废同人多事项草稿，advance_case 不再外发", () => {
  const seed = camilaWithTwoCases();
  const ids = seed.cases.filter((item) => item.creatorId === "camila").map((item) => item.id);
  assert.equal(ids.length, 2);
  const beforeSends = outboundCount(seed);
  const controlled = demoReducer(seed, {type: "control", creatorId: "camila", mode: "human"});
  assert.equal(relationFor(controlled, "camila").control, "human");
  for (const id of ids) assert.equal(caseById(controlled, id).draftValid, false);
  const advanced = reduce(controlled, ...ids.map((caseId) => ({type: "advance_case", caseId})));
  assert.equal(outboundCount(advanced), beforeSends);
});

test("商业决定中的人工接管也必须作废同关系所有草稿", () => {
  const seed = camilaWithTwoCases();
  const target = activeCaseFor(seed, "camila", "watch-mx");
  target.status = "needs_operator";
  const state = demoReducer(seed, {
    type: "resolve_decision", caseId: target.id, choice: "manual", note: "合成测试：交由运营处理",
  });
  assert.equal(relationFor(state, "camila").control, "human");
  assert.ok(state.cases.filter((item) => item.creatorId === "camila").every((item) => !item.draftValid),
    "其他商品的旧 AI 草稿不能在同一关系转人工后仍有效");
});

test("unknown 不允许推进，核验只更新原结果且不追加发送", () => {
  const seed = initialState();
  const caseId = "case-pedro-watch-br";
  const beforeSends = outboundCount(seed);
  const blocked = demoReducer(seed, {type: "advance_case", caseId});
  assert.equal(caseById(blocked, caseId).sendState, "unknown");
  assert.equal(outboundCount(blocked), beforeSends);
  const verified = demoReducer(blocked, {type: "simulate_event", caseId, event: "verify_accepted"});
  assert.equal(caseById(verified, caseId).sendState, "accepted");
  assert.equal(caseById(verified, caseId).delivered, true);
  assert.equal(caseById(verified, caseId).adopted, false);
  assert.equal(outboundCount(verified), beforeSends, "核验不得伪装为再次发送");
  const repeated = reduce(verified,
    {type: "simulate_event", caseId, event: "verify_accepted"},
    {type: "advance_case", caseId},
  );
  assert.equal(outboundCount(repeated), beforeSends);
  assert.deepEqual(caseById(repeated, caseId).messages, caseById(verified, caseId).messages);
});

test("历史导入与核实不发信，未核实不能接续，接续重复运行不建重复事项", () => {
  const seed = initialState();
  const loaded = demoReducer(seed, {type: "hydrate", state: structuredClone(seed)});
  const beforeSends = outboundCount(loaded);
  const beforeCases = loaded.cases.length;
  const unchecked = demoReducer(loaded, {type: "resume_history", id: "h1"});
  assert.equal(unchecked.cases.length, beforeCases);
  const checked = demoReducer(unchecked, {type: "check_history", id: "h1"});
  assert.equal(outboundCount(checked), beforeSends);
  assert.equal(checked.cases.length, beforeCases);
  const once = demoReducer(checked, {type: "resume_history", id: "h1"});
  const twice = demoReducer(once, {type: "resume_history", id: "h1"});
  assert.equal(activeCases(twice, "camila", "watch-mx").length, 1);
  assert.equal(twice.cases.length, once.cases.length);
  assert.equal(outboundCount(twice), beforeSends);
  assert.equal(twice.histories.find((item) => item.id === "h1").caseId,
    once.histories.find((item) => item.id === "h1").caseId);
  const terminal = reduce(twice,
    {type: "resume_history", id: "h3"},
    {type: "resume_history", id: "h4"},
  );
  assert.equal(terminal.cases.length, twice.cases.length, "已过期或已解决历史不能被接续按钮重开");
});

test("已发送未采用不计合作完成；adopted 只有已交付且非 unknown 才成立", () => {
  const seed = initialState();
  const caseId = "case-sofia-watch-mx";
  const premature = demoReducer(seed, {type: "simulate_event", caseId, event: "adopted"});
  assert.equal(caseById(premature, caseId).adopted, false);
  const sent = demoReducer(seed, {type: "advance_case", caseId});
  assert.equal(caseById(sent, caseId).sendState, "accepted");
  assert.equal(caseById(sent, caseId).adopted, false);
  assert.notEqual(caseById(sent, caseId).status, "closed");
  assert.equal(completedCooperations(sent).length, completedCooperations(seed).length);
  const unknown = reduce(sent,
    {type: "simulate_event", caseId, event: "send_unknown"},
    {type: "simulate_event", caseId, event: "adopted"},
  );
  assert.equal(caseById(unknown, caseId).adopted, false);
  const adopted = demoReducer(sent, {type: "simulate_event", caseId, event: "adopted"});
  assert.equal(caseById(adopted, caseId).status, "closed");
  assert.equal(caseById(adopted, caseId).adopted, true);
  assert.equal(completedCooperations(adopted).length, completedCooperations(seed).length + 1);
  assert.equal(outboundCount(adopted), outboundCount(sent));
});

for (const opportunityId of ["o1", "synthetic-sofia-new-product"]) {
  test(`拒联后不能通过机会 ${opportunityId} 重新启动同品或他品营销`, () => {
    const seed = optedOutSofia();
    const state = demoReducer(seed, {type: "activate_opportunity", id: opportunityId});
    assert.equal(state.cases.filter((item) => item.creatorId === "sofia" && item.status !== "closed").length, 0);
    assert.equal(state.cases.length, seed.cases.length, "拒联不能被新机会 ID 绕过");
    assert.equal(outboundCount(state), outboundCount(seed));
  });
}

test("交还 AI 控制不隐式解除明确的营销拒联", () => {
  const seed = optedOutSofia();
  const state = reduce(seed,
    {type: "control", creatorId: "sofia", mode: "ai"},
    {type: "activate_opportunity", id: "synthetic-sofia-new-product"},
  );
  assert.equal(state.cases.filter((item) => item.creatorId === "sofia" && item.status !== "closed").length, 0);
  assert.equal(outboundCount(state), outboundCount(seed));
});

for (const event of ["ask_later", "sample_requested"]) {
  test(`${event} 建立等待后，推进按钮不能立即提交旧营销稿`, () => {
    const caseId = "case-sofia-watch-mx";
    const waiting = demoReducer(initialState(), {type: "simulate_event", caseId, event});
    const advanced = demoReducer(waiting, {type: "advance_case", caseId});
    assert.equal(outboundCount(advanced), outboundCount(waiting));
    assert.equal(caseById(advanced, caseId).sendState, caseById(waiting, caseId).sendState);
    assert.equal(caseById(advanced, caseId).delivered, caseById(waiting, caseId).delivered);
  });
}

test("经营目标暂停后，尚未提交的事项不能继续外发", () => {
  const seed = initialState();
  const goalId = caseById(seed, "case-sofia-watch-mx").goalId;
  const paused = demoReducer(seed, {type: "update_goal", id: goalId, patch: {status: "paused"}});
  const state = demoReducer(paused, {type: "advance_case", caseId: "case-sofia-watch-mx"});
  assert.equal(outboundCount(state), outboundCount(paused));
  assert.equal(caseById(state, "case-sofia-watch-mx").sendState, "not_sent");
});

test("市场源数据一致，激活机会不能把 IT 一发挂入不包含 IT 的目标", () => {
  assert.deepEqual(MARKETS, {
    mx: {name: "墨西哥", locale: "es-MX", currency: "MXN"},
    br: {name: "巴西", locale: "pt-BR", currency: "BRL"},
    it: {name: "意大利", locale: "it-IT", currency: "EUR"},
  });
  let state = initialState();
  for (const opportunity of state.opportunities) {
    assert.equal(creatorFor(state, opportunity.creatorId).market, productFor(state, opportunity.productId).market);
    state = demoReducer(state, {type: "activate_opportunity", id: opportunity.id});
  }
  for (const item of state.cases) {
    const market = creatorFor(state, item.creatorId).market;
    assert.equal(productFor(state, item.productId).market, market);
    const goal = state.goals.find((candidate) => candidate.id === item.goalId);
    assert.ok(goal, "事项必须引用存在的目标");
    assert.ok(goal.markets.includes(market), `${item.id} 的目标 ${goal.id} 不覆盖 ${market}`);
  }
});

const eventLanguageFixtures = [
  {market: "mx", caseId: "case-sofia-watch-mx", sample: /muestra|solicité|solicitud/i, item: /producto|compartes|ficha/i},
  {market: "br", caseId: "case-marina-buds-br", sample: /amostra|solicitei/i, item: /produto|compartilhar|cartão/i},
  {market: "it", caseId: "case-luca-tablet-it", sample: /campione|richiest/i, item: /prodotto|scheda|link/i},
];
for (const fixture of eventLanguageFixtures) {
  for (const [event, pattern] of [["sample_requested", fixture.sample], ["received_item_request", fixture.item]]) {
    test(`${fixture.market} 的 ${event} 模拟原文与达人市场一致`, () => {
      const state = demoReducer(initialState(), {type: "simulate_event", caseId: fixture.caseId, event});
      const messages = caseById(state, fixture.caseId).messages.filter((message) => message.direction === "creator");
      assert.match(messages.at(-1).text, pattern, "演示入站原文不应沿用其他市场的固定语言");
    });
  }
}
