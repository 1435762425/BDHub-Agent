"use client";

import { useEffect, useRef, useState } from "react";
import {
  activeCaseFor,
  creatorFor,
  demoReducer,
  initialState,
  MARKETS,
  money,
  productFor,
  type AgentConfig,
  type DemoState,
  type HistoryItem,
  type Market,
  type Product,
} from "./model";
import { useDemo } from "./store";
import {
  Avatar,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Icon,
  Input,
  MarketPill,
  Notice,
  PageHeading,
  Pill,
  Select,
  Tabs,
  TextArea,
  type Tone,
} from "./ui";

type AgentTab = "capabilities" | "messaging" | "history" | "evaluation";
type HistoryFilter = "all" | HistoryItem["status"];
type ReplayId = "existing-item" | "human-takeover" | "unknown-result";
type ReplayResult = {
  id: ReplayId;
  title: string;
  trace: string[];
  checks: { title: string; passed: boolean; detail: string }[];
};

const TAB_ITEMS: { value: AgentTab; label: string }[] = [
  { value: "capabilities", label: "职责与能力" },
  { value: "messaging", label: "话术策略" },
  { value: "history", label: "知识与历史" },
  { value: "evaluation", label: "回放评估" },
];
const SKILLS = [
  { id: "existing-item", name: "已有实物与商品卡", description: "识别已有实物的诉求，核对同款和方案，不重复要求寄样。", icon: "box" as const },
  { id: "invitation", name: "个性化合作邀约", description: "按商品、市场与当前关系组织说明，合并同人的其他机会。", icon: "chat" as const },
  { id: "sample-status", name: "样品状态服务", description: "查询申请与物流事实，区分正常等待和需要人决定的问题。", icon: "time" as const },
];
const HISTORY_META: Record<HistoryItem["status"], { name: string; description: string; tone: Tone }> = {
  resolved: { name: "已解决", description: "保留已完成的依据", tone: "success" },
  expired: { name: "已过期", description: "旧条件已不再适用", tone: "neutral" },
  actionable: { name: "仍可行动", description: "核实后接续当前需求", tone: "brand" },
  unknown: { name: "状态不明", description: "先核实今天的事实", tone: "warning" },
};
const REPLAY_CASES: { id: ReplayId; title: string; description: string; input: string }[] = [
  { id: "existing-item", title: "已有实物，索取同款商品卡", description: "演示交付后等待采用；不把接收写成成交。", input: "Sofía · 墨西哥 · 运动智能手表" },
  { id: "human-takeover", title: "运营接管时，旧草稿正在准备", description: "先更新控制权，再检查自动推进被阻止。", input: "Sofía · 人工接管 · 未提交草稿" },
  { id: "unknown-result", title: "商品卡超时，结果仍然未知", description: "保留原事项，只核验原结果，不盲目重发。", input: "Pedro · 巴西 · 结果待核验" },
];
const REFERENCE_CASES = [
  {
    id: "reference-existing", title: "已有实物，不重复要求样品", market: "mx" as Market,
    original: "Ya tengo el reloj, ¿me compartes la ficha?", translation: "我已经有手表，可以给我对应商品卡吗？",
    response: "先确认具体商品和当前方案；条件齐备后交付对应商品卡，接收与采用分别记录。",
    facts: ["达人明确表示持有实物", "精确商品与当前有效方案", "同关系是否已有待发或未知行动"],
    avoid: "不能只凭历史带货推定仍有实物，也不重复索要无关信息。",
  },
  {
    id: "reference-sample", title: "样品待审，准确解释正常等待", market: "br" as Market,
    original: "Já solicitei a amostra. Tem alguma novidade?", translation: "我已经申请样品了，有进展吗？",
    response: "查询当前申请状态，用已核实的事实解释进度；等待状态变化，不把正常待审全部交给运营。",
    facts: ["本市场的样品处理路线", "真实申请记录与最近状态时间", "是否已有承诺或人工处理"],
    avoid: "不承诺必然批准、已发货或到货日期；示例不是今天的实际状态。",
  },
  {
    id: "reference-unknown", title: "结果未知，先核验原意图", market: "br" as Market,
    original: "Pode enviar a ficha do produto?", translation: "可以发给我商品卡吗？",
    response: "提交后超时则保存未知状态；先只读核验原发送结果，查到证据后继续同一事项。",
    facts: ["原发送意图与消息组件", "平台记录和接收证据", "相关行动是否已经暂停"],
    avoid: "不能换账号重发，不能把未知改成成功，也不能重发整个消息组合。",
  },
];
const PREVIEW_SAMPLE: Record<Market, { creatorId: string; productId: string; productName: string }> = {
  mx: { creatorId: "camila", productId: "watch-mx", productName: "reloj inteligente deportivo" },
  br: { creatorId: "pedro", productId: "buds-br", productName: "fone sem fio com cancelamento de ruído" },
  it: { creatorId: "andrea", productId: "tablet-it", productName: "tablet leggero" },
};

function normalizeTab(value?: string): AgentTab {
  if (value === "messaging" || value === "history" || value === "evaluation") return value;
  return "capabilities";
}

function localMessage(market: Market, name: string, product: Product, mode: AgentConfig["messageMode"], tone: AgentConfig["tone"], variation: number) {
  const firstName = name.split(" ")[0];
  const item = PREVIEW_SAMPLE[market].productName;
  const commission = product.commission;
  const alternate = variation % 2 === 1;
  const fixed: Record<Market, { text: string; translation: string }> = {
    mx: { text: `Hola, ${firstName}. Somos BJN. Tenemos una propuesta para un ${item}, con una comisión del ${commission} %. ¿Te gustaría conocer las condiciones?`, translation: `你好，${firstName}，我们是 BJN。这是一款${product.name}的合作方案，佣金为 ${commission}%。你愿意了解具体条件吗？` },
    br: { text: `Olá, ${firstName}. Somos a BJN. Temos uma proposta para um ${item}, com comissão de ${commission}%. Gostaria de conhecer as condições?`, translation: `你好，${firstName}，我们是 BJN。这是一款${product.name}的合作方案，佣金为 ${commission}%。你愿意了解具体条件吗？` },
    it: { text: `Ciao, ${firstName}. Siamo BJN. Abbiamo una proposta per un ${item}, con una commissione dell’${commission}%. Ti interessa conoscere le condizioni?`, translation: `你好，${firstName}，我们是 BJN。这是一款${product.name}的合作方案，佣金为 ${commission}%。你愿意了解具体条件吗？` },
  };
  if (mode === "fixed") return fixed[market];
  const greetings: Record<Market, Record<AgentConfig["tone"], string>> = {
    mx: { friendly: `¡Hola, ${firstName}!`, direct: `Hola, ${firstName}.`, patient: `Hola, ${firstName}, espero que estés bien.` },
    br: { friendly: `Oi, ${firstName}!`, direct: `Olá, ${firstName}.`, patient: `Olá, ${firstName}, espero que esteja bem.` },
    it: { friendly: `Ciao, ${firstName}!`, direct: `Ciao, ${firstName}.`, patient: `Ciao, ${firstName}, spero che tu stia bene.` },
  };
  const bodies: Record<Market, string[]> = {
    mx: [
      `Desde BJN queremos presentarte una propuesta para un ${item}. La comisión de esta propuesta es del ${commission} %. Si te interesa, podemos compartirte los detalles del producto.`,
      `Tenemos disponible una propuesta para un ${item}. La comisión de esta propuesta es del ${commission} %. ¿Te gustaría revisar los detalles con BJN?`,
    ],
    br: [
      `A BJN gostaria de apresentar uma proposta para um ${item}. A comissão desta proposta é de ${commission}%. Se tiver interesse, podemos compartilhar os detalhes do produto.`,
      `Temos uma proposta disponível para um ${item}. A comissão desta proposta é de ${commission}%. Gostaria de conferir os detalhes com a BJN?`,
    ],
    it: [
      `BJN vorrebbe presentarti una proposta per un ${item}. La commissione di questa proposta è dell’${commission}%. Se ti interessa, possiamo condividere i dettagli del prodotto.`,
      `Abbiamo una proposta disponibile per un ${item}. La commissione di questa proposta è dell’${commission}%. Ti va di conoscere i dettagli con BJN?`,
    ],
  };
  const patientEnd: Record<Market, string> = {
    mx: "Puedes revisarlo cuando te venga bien.", br: "Você pode conferir quando for conveniente.", it: "Puoi valutarla quando ti è più comodo.",
  };
  return {
    text: `${greetings[market][tone]}${mode === "generated" ? "\n\n" : " "}${bodies[market][alternate ? 1 : 0]}${tone === "patient" ? ` ${patientEnd[market]}` : ""}`,
    translation: `你好，${firstName}。${alternate ? "我们有一份可以了解的" : "BJN 想向你介绍一份"}${product.name}合作方案。这份方案的佣金为 ${commission}%。${alternate ? "你愿意和 BJN 一起查看具体条件吗？" : "如果你有兴趣，我们可以继续提供商品详情。"}${tone === "patient" ? "你可以在方便的时候查看。" : ""}`,
  };
}

function caseById(state: DemoState, id: string) {
  const item = state.cases.find((entry) => entry.id === id);
  if (!item) throw new Error("预置回放事项不存在，请检查本地演示样本。");
  return item;
}

function runReplay(id: ReplayId): ReplayResult {
  const definition = REPLAY_CASES.find((item) => item.id === id)!;
  let isolated = initialState();
  if (id === "existing-item") {
    const caseId = "case-sofia-watch-mx";
    const beforeCount = isolated.cases.length;
    isolated = demoReducer(isolated, { type: "advance_case", caseId });
    const result = caseById(isolated, caseId);
    return {
      id, title: definition.title,
      trace: ["加载独立的已有实物样本", "调用本地 advance_case 演示动作", "检查接收、采用与事项数量"],
      checks: [
        { title: "方案已演示交付", passed: result.delivered && result.sendState === "accepted", detail: `交付=${result.delivered}；发送状态=${result.sendState}` },
        { title: "接收与采用分别记录", passed: !result.adopted && result.status === "waiting_creator", detail: `采用=${result.adopted}；事项阶段=${result.status}` },
        { title: "继续同一事项", passed: isolated.cases.length === beforeCount && !result.draftValid, detail: `事项数 ${beforeCount} → ${isolated.cases.length}；旧稿有效=${result.draftValid}` },
      ],
    };
  }
  if (id === "human-takeover") {
    const caseId = "case-sofia-watch-mx";
    isolated = demoReducer(isolated, { type: "control", creatorId: "sofia", mode: "human" });
    const before = caseById(isolated, caseId);
    const beforeMessages = before.messages.filter((item) => item.by === "ai").length;
    isolated = demoReducer(isolated, { type: "advance_case", caseId });
    const result = caseById(isolated, caseId);
    return {
      id, title: definition.title,
      trace: ["加载独立的未提交草稿样本", "先调用本地 control 切为人工", "尝试 advance_case，检查自动提交仍被阻止"],
      checks: [
        { title: "控制权保持人工", passed: isolated.relations.find((item) => item.creatorId === "sofia")?.control === "human", detail: "检查关系控制字段，不依据按钮提示判断。" },
        { title: "旧草稿已失效", passed: !result.draftValid, detail: `旧稿有效=${result.draftValid}` },
        { title: "没有新增自动消息", passed: result.messages.filter((item) => item.by === "ai").length === beforeMessages && result.sendState === "not_sent", detail: `AI 消息 ${beforeMessages} → ${result.messages.filter((item) => item.by === "ai").length}；发送状态=${result.sendState}` },
      ],
    };
  }
  const caseId = "case-pedro-watch-br";
  const beforeCount = isolated.cases.length;
  const beforeMessages = caseById(isolated, caseId).messages.filter((item) => item.by === "ai").length;
  isolated = demoReducer(isolated, { type: "advance_case", caseId });
  const blocked = caseById(isolated, caseId);
  const blockedCorrectly = blocked.sendState === "unknown" && blocked.messages.filter((item) => item.by === "ai").length === beforeMessages;
  isolated = demoReducer(isolated, { type: "simulate_event", caseId, event: "verify_accepted" });
  isolated = demoReducer(isolated, { type: "advance_case", caseId });
  const result = caseById(isolated, caseId);
  return {
    id, title: definition.title,
    trace: ["加载独立的结果未知样本", "尝试推进，检查没有重发", "模拟原意图已接收证据，再检查等待采用"],
    checks: [
      { title: "未知期间不重复提交", passed: blockedCorrectly, detail: "核验前仍为 unknown，AI 消息数未增加。" },
      { title: "仅核验原事项", passed: result.sendState === "accepted" && isolated.cases.length === beforeCount, detail: `发送状态=${result.sendState}；事项数 ${beforeCount} → ${isolated.cases.length}` },
      { title: "接收后仍未补发或判为采用", passed: result.messages.filter((item) => item.by === "ai").length === beforeMessages && !result.adopted && result.status === "waiting_creator", detail: `AI 消息=${result.messages.filter((item) => item.by === "ai").length}；采用=${result.adopted}；阶段=${result.status}` },
    ],
  };
}

export default function AgentsPage({ initialTab }: { initialTab?: string }) {
  const { state, dispatch, notify, go, openCase, hydrated } = useDemo();
  const [tab, setTab] = useState<AgentTab>(() => normalizeTab(initialTab));
  const [instructions, setInstructions] = useState(state.agent.instructions);
  const [skills, setSkills] = useState<string[]>(() => [...state.agent.skills]);
  const [messageMode, setMessageMode] = useState<AgentConfig["messageMode"]>(state.agent.messageMode);
  const [tone, setTone] = useState<AgentConfig["tone"]>(state.agent.tone);
  const [messageRule, setMessageRule] = useState(state.agent.messageRule);
  const [configError, setConfigError] = useState("");
  const [messageError, setMessageError] = useState("");
  const [previewMarket, setPreviewMarket] = useState<Market>(state.marketFilter === "all" ? "mx" : state.marketFilter);
  const [variation, setVariation] = useState(0);
  const [copying, setCopying] = useState(false);
  const [copyError, setCopyError] = useState("");
  const [historyFilter, setHistoryFilter] = useState<HistoryFilter>("all");
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyDetail, setHistoryDetail] = useState<string | null>(null);
  const [referenceId, setReferenceId] = useState<string | null>(null);
  const [selectedReplays, setSelectedReplays] = useState<ReplayId[]>(REPLAY_CASES.map((item) => item.id));
  const [replayResults, setReplayResults] = useState<ReplayResult[] | null>(null);
  const [replayAt, setReplayAt] = useState("");
  const initialized = useRef(hydrated);
  const pendingHistory = useRef<string | null>(null);

  useEffect(() => { setTab(normalizeTab(initialTab)); }, [initialTab]);
  useEffect(() => {
    if (!hydrated || initialized.current) return;
    initialized.current = true;
    setInstructions(state.agent.instructions);
    setSkills([...state.agent.skills]);
    setMessageMode(state.agent.messageMode);
    setTone(state.agent.tone);
    setMessageRule(state.agent.messageRule);
  }, [hydrated, state.agent]);
  useEffect(() => {
    if (!pendingHistory.current) return;
    const item = state.histories.find((entry) => entry.id === pendingHistory.current);
    if (!item?.caseId || !state.cases.some((entry) => entry.id === item.caseId)) return;
    pendingHistory.current = null;
    notify("历史需求已关联到模拟合作事项，没有发送新消息。");
    openCase(item.caseId);
  }, [state.histories, state.cases, notify, openCase]);

  const capabilityDirty = instructions !== state.agent.instructions || skills.join("|") !== state.agent.skills.join("|");
  const messageDirty = messageMode !== state.agent.messageMode || tone !== state.agent.tone || messageRule !== state.agent.messageRule;
  const sample = PREVIEW_SAMPLE[previewMarket];
  const sampleCreator = creatorFor(state, sample.creatorId);
  const sampleProduct = productFor(state, sample.productId);
  const preview = localMessage(previewMarket, sampleCreator.name, sampleProduct, messageMode, tone, variation);
  const histories = state.histories.filter((item) => {
    const creator = creatorFor(state, item.creatorId);
    return (historyFilter === "all" || item.status === historyFilter) && `${item.question} ${creator.name} ${creator.handle}`.toLowerCase().includes(historyQuery.trim().toLowerCase());
  });
  const detail = state.histories.find((item) => item.id === historyDetail);
  const reference = REFERENCE_CASES.find((item) => item.id === referenceId);
  const checks = replayResults?.flatMap((item) => item.checks) || [];

  function saveCapabilities() {
    if (!instructions.trim()) { setConfigError("请填写关系处理职责，再保存配置。"); return; }
    setConfigError("");
    dispatch({ type: "save_agent", patch: { instructions: instructions.trim(), skills } });
    setInstructions(instructions.trim());
    notify(`职责与能力已保存为 v${state.agent.version + 1}，仅更新浏览器演示配置。`);
  }
  function saveMessaging() {
    if (!messageRule.trim()) { setMessageError("请保留一条明确的话术规则，再保存策略。"); return; }
    setMessageError("");
    dispatch({ type: "save_agent", patch: { messageMode, tone, messageRule: messageRule.trim() } });
    setMessageRule(messageRule.trim());
    notify(`话术策略已保存为 v${state.agent.version + 1}，没有调用真实模型。`);
  }
  function checkHistory(item: HistoryItem) {
    dispatch({ type: "check_history", id: item.id });
    setHistoryDetail(item.id);
    notify("已按本地预设证据完成模拟核实；没有查询真实平台或补发旧消息。");
  }
  function resumeHistory(item: HistoryItem) {
    if (!item.checked || item.status !== "actionable") { notify("先核实当前事实，只有仍可行动的事项可以接续。"); return; }
    if (pendingHistory.current) return;
    setHistoryDetail(null);
    if (item.caseId && state.cases.some((entry) => entry.id === item.caseId)) { openCase(item.caseId); return; }
    pendingHistory.current = item.id;
    dispatch({ type: "resume_history", id: item.id });
  }
  async function copyPreview() {
    setCopying(true); setCopyError("");
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(preview.text);
      notify("已复制当前市场的预设原文，没有发送消息。");
    } catch {
      setCopyError("浏览器未允许复制。请展开原文并手动选择复制。");
      notify("复制未完成，可在原文区域手动选择复制。");
    } finally { setCopying(false); }
  }
  function runSelectedReplays() {
    if (!selectedReplays.length) { notify("请至少选择一个预设场景。"); return; }
    const results = selectedReplays.map((id): ReplayResult => {
      try { return runReplay(id); }
      catch (error) {
        return { id, title: REPLAY_CASES.find((entry) => entry.id === id)!.title, trace: ["读取本地预设样本"], checks: [{ title: "回放样本可运行", passed: false, detail: error instanceof Error ? error.message : "本地回放未能完成。" }] };
      }
    });
    setReplayResults(results);
    setReplayAt(new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    notify(`已完成 ${results.length} 个离线演示场景；没有调用模型或改变经营事项。`);
  }

  return (
    <div>
      <PageHeading title="Agent 与知识" description="配置处理规则、话术与参考案例，让每段关系的下一步有据可查。" action={<Pill tone="brand">演示配置 v{state.agent.version}</Pill>} />
      <div className="mb-6"><Tabs items={TAB_ITEMS} value={tab} onChange={setTab} /></div>

      {tab === "capabilities" && (
        <div className="grid gap-6 xl:grid-cols-5">
          <Card title="关系处理定义" subtitle="同一位达人的商品、消息与未完成承诺共用这一职责定义。" className="xl:col-span-3" action={capabilityDirty ? <Pill tone="warning">有未保存修改</Pill> : <Pill tone="success">已保存</Pill>}>
            <form className="space-y-6 p-5 sm:p-6" onSubmit={(event) => { event.preventDefault(); saveCapabilities(); }}>
              <Field label="处理职责" hint="配置保存在当前浏览器。职责不增加执行权限，真实能力由业务规则与执行器决定。">
                <TextArea rows={7} value={instructions} disabled={!hydrated} onChange={(event) => { setInstructions(event.target.value); setConfigError(""); }} aria-invalid={!!configError} placeholder="说明如何处理当前诉求、已有承诺与商业例外…" />
              </Field>
              <fieldset disabled={!hydrated} className="space-y-3">
                <legend className="mb-3 text-sm font-medium text-gray-700 dark:text-gray-300">按场景启用能力 <span className="ml-1 text-gray-400">{skills.length} 项</span></legend>
                {SKILLS.map((skill) => (
                  <label key={skill.id} className={`flex cursor-pointer items-start gap-3 rounded-xl border p-4 transition ${skills.includes(skill.id) ? "border-brand-200 bg-brand-50/50 dark:border-brand-800 dark:bg-brand-500/5" : "border-gray-200 dark:border-gray-800"}`}>
                    <input type="checkbox" checked={skills.includes(skill.id)} onChange={(event) => setSkills((current) => event.target.checked ? [...current, skill.id] : current.filter((entry) => entry !== skill.id))} className="mt-1 size-4 shrink-0 accent-brand-500" />
                    <Icon name={skill.icon} className="mt-0.5 size-5 shrink-0 text-gray-400" />
                    <span><span className="block text-sm font-medium text-gray-800 dark:text-gray-200">{skill.name}</span><span className="mt-1 block text-xs leading-5 text-gray-500 dark:text-gray-400">{skill.description}</span></span>
                  </label>
                ))}
              </fieldset>
              {configError && <div role="alert"><Notice tone="warning">{configError}</Notice></div>}
              <div className="flex flex-wrap items-center gap-3 border-t border-gray-100 pt-5 dark:border-gray-800">
                <Button type="submit" disabled={!hydrated || !capabilityDirty}><Icon name="check" />保存职责与能力</Button>
                <Button variant="outline" disabled={!capabilityDirty} onClick={() => { setInstructions(state.agent.instructions); setSkills([...state.agent.skills]); setConfigError(""); notify("已恢复到当前保存的职责与能力。"); }}>恢复已保存配置</Button>
                <span className="text-xs text-gray-500">当前版本 v{state.agent.version}</span>
              </div>
            </form>
          </Card>
          <div className="space-y-6 xl:col-span-2">
            <Card title="如何分工" subtitle="这是可复用的职责与能力，不是三位持续在线的虚拟员工。">
              <div className="divide-y divide-gray-100 px-5 dark:divide-gray-800">
                {[
                  { title: "关系处理定义", trigger: "有新消息、业务变化或到期事项时", text: "读取当前关系与事实，提出一个协调后的下一步；同一达人跨商品共享上下文。", icon: "users" as const },
                  { title: "按需机会分析", trigger: "有新货盘或新增候选时", text: "分析适配与缺失事实，返回有依据的候选；不独立联系达人。", icon: "search" as const },
                  { title: "离线评估流水线", trigger: "版本检查或抽样回放时", text: "检查事实一致性与动作边界，形成改进依据；不自动改商业条件。", icon: "chart" as const },
                ].map((role) => <div key={role.title} className="flex gap-3 py-5"><span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-500 dark:bg-gray-800"><Icon name={role.icon} /></span><div><h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">{role.title}</h3><p className="mt-1 text-xs text-brand-500">{role.trigger}</p><p className="mt-2 text-xs leading-6 text-gray-500 dark:text-gray-400">{role.text}</p></div></div>)}
              </div>
            </Card>
            <Notice>队列、去重、暂停、预算与唤醒由程序处理。等待期间不需要一直运行模型；本原型没有调用模型服务。</Notice>
            <div className="flex flex-wrap gap-3"><Button variant="outline" onClick={() => go("goals")}><Icon name="goal" />查看经营计划</Button><Button variant="outline" onClick={() => setTab("evaluation")}><Icon name="chart" />试试离线回放</Button></div>
          </div>
        </div>
      )}

      {tab === "messaging" && (
        <div className="space-y-6">
          <Notice>预览使用本地预设的一发邀约样例，没有调用真实模型。自由文本规则会保存并展示为依据，当前不会由模型理解或执行。</Notice>
          <div className="grid items-start gap-6 xl:grid-cols-5">
            <Card title="话术策略" subtitle="定义表达边界；单次预览不会发送给达人。" className="xl:col-span-2" action={messageDirty ? <Pill tone="warning">未保存</Pill> : <Pill tone="success">已保存</Pill>}>
              <form className="space-y-5 p-5 sm:p-6" onSubmit={(event) => { event.preventDefault(); saveMessaging(); }}>
                <Field label="生成模式"><Select value={messageMode} disabled={!hydrated} onChange={(event) => { setMessageMode(event.target.value as AgentConfig["messageMode"]); setVariation(0); setCopyError(""); }}><option value="fixed">固定正文</option><option value="skeleton">固定骨架 + AI 表达</option><option value="generated">按策略生成</option></Select></Field>
                <p className="-mt-2 text-xs leading-6 text-gray-500">{messageMode === "fixed" ? "使用预设固定正文；语气和换表达不改变正文。" : messageMode === "skeleton" ? "保留商品与商业条件，用本地样例切换开场和行动请求。" : "展示按上下文组织全文的预期形式；这里仍是本地文字样例。"}</p>
                <Field label="语气" hint={messageMode === "fixed" ? "固定正文模式下保留该设置，切换到其他模式后应用于预览。" : undefined}><Select value={tone} disabled={!hydrated || messageMode === "fixed"} onChange={(event) => setTone(event.target.value as AgentConfig["tone"])}><option value="friendly">自然友好</option><option value="direct">简短直接</option><option value="patient">耐心解释</option></Select></Field>
                <Field label="必须遵循的规则" hint="规则只作为配置保存，不会自动改变全局商业权限。"><TextArea rows={5} disabled={!hydrated} value={messageRule} onChange={(event) => { setMessageRule(event.target.value); setMessageError(""); }} aria-invalid={!!messageError} /></Field>
                {messageError && <div role="alert"><Notice tone="warning">{messageError}</Notice></div>}
                <div className="flex flex-wrap gap-3 border-t border-gray-100 pt-5 dark:border-gray-800"><Button type="submit" disabled={!hydrated || !messageDirty}>保存话术策略</Button><Button variant="outline" disabled={!messageDirty} onClick={() => { setMessageMode(state.agent.messageMode); setTone(state.agent.tone); setMessageRule(state.agent.messageRule); setMessageError(""); setVariation(0); notify("已恢复到当前保存的话术策略。"); }}>恢复已保存</Button></div>
              </form>
            </Card>
            <Card title="个性化预览" subtitle="切换市场比较原文、中文释义和样例依据。" className="xl:col-span-3" action={<Pill tone="info">本地预设 · 未发送</Pill>}>
              <div className="space-y-5 p-5 sm:p-6">
                <div className="grid gap-4 sm:grid-cols-2"><Field label="预览市场"><Select value={previewMarket} onChange={(event) => { setPreviewMarket(event.target.value as Market); setVariation(0); setCopyError(""); }}>{(Object.keys(MARKETS) as Market[]).map((market) => <option key={market} value={market}>{MARKETS[market].name} · {MARKETS[market].locale}</option>)}</Select></Field><div className="flex items-center gap-3 self-end rounded-xl bg-gray-50 p-3 dark:bg-gray-800/60"><Avatar src={sampleCreator.avatar} name={sampleCreator.name} size={36} /><div><p className="text-sm font-medium text-gray-800 dark:text-gray-200">{sampleCreator.name}</p><span className="text-xs text-gray-500">预设邀约样本 · 不改变现有会话</span></div></div></div>
                <details open className="rounded-xl border border-gray-200 dark:border-gray-800"><summary className="cursor-pointer px-4 py-3 text-sm font-medium text-gray-800 dark:text-gray-200">原文 · {MARKETS[previewMarket].locale}</summary><div className="border-t border-gray-100 px-4 py-4 dark:border-gray-800"><p lang={MARKETS[previewMarket].locale} className="select-text whitespace-pre-wrap text-sm leading-7 text-gray-700 dark:text-gray-300">{preview.text}</p></div></details>
                <details className="rounded-xl border border-gray-200 dark:border-gray-800"><summary className="cursor-pointer px-4 py-3 text-sm font-medium text-gray-800 dark:text-gray-200">中文辅助理解 · 样例释义</summary><p className="border-t border-gray-100 px-4 py-4 text-sm leading-7 text-gray-500 dark:border-gray-800 dark:text-gray-400">{preview.translation}</p></details>
                <details className="rounded-xl border border-gray-200 dark:border-gray-800"><summary className="cursor-pointer px-4 py-3 text-sm font-medium text-gray-800 dark:text-gray-200">本次样例的依据与规则</summary><div className="space-y-4 border-t border-gray-100 px-4 py-4 dark:border-gray-800"><div className="flex items-center gap-3"><img src={sampleProduct.image} alt={sampleProduct.name} width={48} height={48} className="size-12 rounded-lg object-cover" /><div><p className="text-sm text-gray-800 dark:text-gray-200">{sampleProduct.name}</p><p className="mt-1 text-xs text-gray-500">{money(sampleProduct)} · 演示佣金 {sampleProduct.commission}%</p></div></div><p className="text-xs leading-6 text-gray-500">商品与佣金来自浏览器演示货盘；没有声称看过具体视频，没有附加真实链接，也不承诺样品审批。当前自由规则仅展示，未由模型解析。</p><div className="rounded-lg bg-gray-50 p-3 text-xs leading-6 text-gray-600 dark:bg-gray-800 dark:text-gray-300">{messageRule.trim() || "尚未填写规则"}</div><p className="text-xs text-gray-400">{messageDirty ? "正在预览未保存的设置" : `已保存配置 v${state.agent.version}`} · {messageMode === "fixed" ? "固定样例" : `表达样例 ${variation % 2 + 1} / 2`}</p></div></details>
                {copyError && <div role="alert"><Notice tone="warning">{copyError}</Notice></div>}
                <div className="flex flex-wrap items-center gap-3"><Button variant="outline" disabled={messageMode === "fixed"} onClick={() => { setVariation((current) => current + 1); setCopyError(""); notify("已切换另一条本地表达样例，未调用模型。"); }}><Icon name="edit" />换个表达</Button><Button variant="outline" disabled={copying} onClick={copyPreview}><Icon name="copy" />{copying ? "复制中…" : "复制原文"}</Button><span className="text-xs text-gray-400">预览不发送</span></div>
              </div>
            </Card>
          </div>
        </div>
      )}

      {tab === "history" && (
        <div className="space-y-6">
          <Notice>墨西哥历史样本按事项分为四类，它们是并列分类。先核实今天的事实，再接续仍有效的需求；导入和查看不会补发旧消息。</Notice>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{(Object.keys(HISTORY_META) as HistoryItem["status"][]).map((status) => <button key={status} type="button" aria-pressed={historyFilter === status} onClick={() => setHistoryFilter(status)} className={`rounded-2xl border bg-white p-5 text-left transition dark:bg-white/[0.025] ${historyFilter === status ? "border-brand-400 ring-2 ring-brand-500/10 dark:border-brand-600" : "border-gray-200 hover:border-gray-300 dark:border-gray-800 dark:hover:border-gray-700"}`}><div className="flex items-center justify-between gap-3"><Pill tone={HISTORY_META[status].tone}>{HISTORY_META[status].name}</Pill><span className="text-2xl font-semibold text-gray-800 dark:text-gray-100">{state.histories.filter((item) => item.status === status).length}</span></div><p className="mt-3 text-xs text-gray-500">{HISTORY_META[status].description}</p></button>)}</div>
          <Card title="历史需求核实" subtitle="当前为 MX 的四条模拟记录，不包含真实达人历史。" action={<Button size="sm" variant="outline" onClick={() => { setHistoryFilter("all"); setHistoryQuery(""); }}>查看全部 {state.histories.length} 条</Button>}>
            <div className="border-b border-gray-100 p-5 dark:border-gray-800"><Field label="搜索历史需求"><Input value={historyQuery} onChange={(event) => setHistoryQuery(event.target.value)} placeholder="达人、Handle 或问题关键词" className="max-w-lg" /></Field></div>
            {histories.length ? <div className="divide-y divide-gray-100 dark:divide-gray-800">{histories.map((item) => { const creator = creatorFor(state, item.creatorId); return <div key={item.id} className="flex flex-wrap items-center gap-4 p-5"><Avatar src={creator.avatar} name={creator.name} /><div className="min-w-48 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold text-gray-800 dark:text-gray-200">{creator.name}</span><MarketPill market={creator.market} /><span className="text-xs text-gray-400">{item.date}</span></div><p className="mt-1.5 text-sm text-gray-600 dark:text-gray-300">{item.question}</p><p className="mt-1 text-xs text-gray-400">{item.checked ? "已完成本地模拟核实" : "等待当前事实核实"}{item.caseId ? " · 已关联合作事项" : ""}</p></div><Pill tone={HISTORY_META[item.status].tone}>{HISTORY_META[item.status].name}</Pill><div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => setHistoryDetail(item.id)}>查看依据</Button>{!item.checked ? <Button size="sm" disabled={!hydrated} onClick={() => checkHistory(item)}>模拟核实</Button> : item.status === "actionable" ? <Button size="sm" disabled={!hydrated} onClick={() => resumeHistory(item)}>{item.caseId ? "打开合作" : "接续需求"}<Icon name="arrow" className="size-4" /></Button> : null}</div></div>; })}</div> : <EmptyState title="没有符合条件的历史事项" description="尝试其他分类或清除搜索，四类不是处理步骤。" action={<Button variant="outline" onClick={() => { setHistoryFilter("all"); setHistoryQuery(""); }}>清除筛选</Button>} />}
          </Card>
          <Card title="参考案例" subtitle="查看认可的处理方式和需要核实的事实；本页不会进行真实训练或自动改规则。"><div className="grid divide-y divide-gray-100 dark:divide-gray-800 lg:grid-cols-3 lg:divide-x lg:divide-y-0">{REFERENCE_CASES.map((item) => <div key={item.id} className="flex flex-col p-5"><MarketPill market={item.market} /><h3 className="mt-3 text-sm font-semibold text-gray-800 dark:text-gray-200">{item.title}</h3><p className="mt-2 flex-1 text-xs leading-6 text-gray-500">{item.response}</p><div className="mt-4"><Button size="sm" variant="outline" onClick={() => setReferenceId(item.id)}>查看处理依据<Icon name="arrow" className="size-4" /></Button></div></div>)}</div></Card>
        </div>
      )}

      {tab === "evaluation" && (
        <div className="space-y-6">
          <Notice>离线演示：在独立的本地样本副本中检查预设流程，不调用模型、工具或平台，也不改变当前经营事项。结果不代表真实模型评测通过。</Notice>
          <Card title="选择核心场景" subtitle="检查动作状态和结果边界；可以单独运行或一起比较。" action={<Pill tone="neutral">{selectedReplays.length} / 3 已选择</Pill>}>
            <div className="grid gap-4 p-5 lg:grid-cols-3">{REPLAY_CASES.map((item) => <label key={item.id} className={`cursor-pointer rounded-xl border p-4 ${selectedReplays.includes(item.id) ? "border-brand-200 bg-brand-50/40 dark:border-brand-800 dark:bg-brand-500/5" : "border-gray-200 dark:border-gray-800"}`}><div className="flex items-start gap-3"><input type="checkbox" checked={selectedReplays.includes(item.id)} onChange={(event) => setSelectedReplays((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))} className="mt-1 size-4 shrink-0 accent-brand-500" /><div><h3 className="text-sm font-semibold leading-6 text-gray-800 dark:text-gray-200">{item.title}</h3><p className="mt-2 text-xs leading-6 text-gray-500">{item.description}</p><p className="mt-3 text-xs text-gray-400">{item.input}</p></div></div></label>)}</div>
            <div className="flex flex-wrap items-center gap-3 border-t border-gray-100 px-5 py-4 dark:border-gray-800"><Button disabled={!selectedReplays.length} onClick={runSelectedReplays}><Icon name="chart" />运行离线演示</Button><Button variant="outline" disabled={selectedReplays.length === 3} onClick={() => setSelectedReplays(REPLAY_CASES.map((item) => item.id))}>选择全部场景</Button><span className="text-xs text-gray-500">只检查本地演示状态机；不分析职责文本和模型输出。</span></div>
          </Card>
          {replayResults ? <div className="space-y-4"><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-base font-semibold text-gray-800 dark:text-gray-200">本次离线检查</h2><div className="flex items-center gap-3"><span className="text-xs text-gray-500">{replayAt} · 仅保留在当前页面</span><Pill tone={checks.every((item) => item.passed) ? "success" : "warning"}>{checks.filter((item) => item.passed).length} / {checks.length} 项本地检查符合</Pill></div></div>{replayResults.map((result) => <Card key={result.id} title={result.title} action={<Pill tone={result.checks.every((item) => item.passed) ? "success" : "warning"}>{result.checks.every((item) => item.passed) ? "本地符合" : "需要检查"}</Pill>}><div className="grid gap-5 p-5 lg:grid-cols-2"><div><h3 className="mb-3 text-xs font-medium uppercase tracking-wide text-gray-400">预设演示轨迹</h3><ol className="space-y-3">{result.trace.map((step, index) => <li key={step} className="flex items-start gap-3 text-sm leading-6 text-gray-600 dark:text-gray-300"><span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-gray-100 text-xs text-gray-500 dark:bg-gray-800">{index + 1}</span>{step}</li>)}</ol></div><div className="space-y-3">{result.checks.map((check) => <div key={check.title} className="flex gap-3 rounded-lg bg-gray-50 p-3 dark:bg-gray-800/60"><Icon name={check.passed ? "check" : "info"} className={`mt-0.5 size-5 shrink-0 ${check.passed ? "text-success-500" : "text-warning-500"}`} /><div><p className="text-sm font-medium text-gray-700 dark:text-gray-200">{check.title}</p><p className="mt-1 break-words text-xs leading-5 text-gray-500">{check.detail}</p></div></div>)}</div></div></Card>)}<Notice>这些结果只证明预设本地动作满足所列条件，不能推断模型理解正确、工具参数正确或真实平台送达。正式评估需要独立样本和实际执行证据。</Notice></div> : <Card><EmptyState title="尚未运行回放" description="选择场景后点击运行，查看预设轨迹和具体检查结果。当前经营数据不会被修改。" /></Card>}
        </div>
      )}

      <Dialog open={!!detail} onClose={() => setHistoryDetail(null)} title="历史需求与当前核实" description="原始问题、当前分类和下一步分别记录。所有内容都是本地演示样本。" wide>
        {detail && (() => {
          const creator = creatorFor(state, detail.creatorId);
          const product = productFor(state, detail.productId);
          const relation = state.relations.find((item) => item.creatorId === detail.creatorId);
          const existing = activeCaseFor(state, detail.creatorId, detail.productId);
          const resumeLabel = detail.caseId ? "打开已关联合作" : existing ? "关联既有合作" : "接续到合作工作台";
          return <div className="space-y-5"><div className="flex flex-wrap items-center gap-3"><Avatar src={creator.avatar} name={creator.name} /><div className="flex-1"><p className="font-medium text-gray-800 dark:text-gray-200">{creator.name}</p><p className="mt-1 text-xs text-gray-500">{detail.date} · {product.name}</p></div><MarketPill market={creator.market} /><Pill tone={HISTORY_META[detail.status].tone}>{HISTORY_META[detail.status].name}</Pill></div><div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800/60"><p lang="es-MX" className="text-sm leading-7 text-gray-700 dark:text-gray-200">{detail.original}</p><p className="mt-3 border-t border-gray-200 pt-3 text-sm leading-6 text-gray-500 dark:border-gray-700">中文理解：{detail.question}</p></div>{detail.checked ? <Notice tone={detail.status === "actionable" ? "success" : "info"}>本地模拟核实结果：{detail.status === "actionable" ? "需求仍有效，可关联当前合作事项；没有补发历史消息。" : detail.status === "resolved" ? "后续已有处理依据，保留结果，不重复发旧回复。" : detail.status === "expired" ? "原问题的活动前提已过期，不能沿用旧方案。" : "当前事实仍不足，需要继续核实。"}</Notice> : <Notice tone="warning">尚未核实今天的事实，不能仅凭原文或未读标记接续。点击模拟核实后，展示该样本预置的核实结果。</Notice>}<div className="grid gap-4 sm:grid-cols-2"><div className="rounded-xl border border-gray-200 p-4 dark:border-gray-800"><h3 className="text-sm font-medium text-gray-700 dark:text-gray-200">关系与事项</h3><p className="mt-2 text-xs leading-6 text-gray-500">当前控制：{relation?.control === "human" ? "人工接管" : relation?.control === "paused" ? "已暂停" : "AI"}<br />{existing ? `已有同款事项：${existing.title}` : "当前没有同款活动事项，接续时只建立一次。"}</p></div><div className="rounded-xl border border-gray-200 p-4 dark:border-gray-800"><h3 className="text-sm font-medium text-gray-700 dark:text-gray-200">模拟核实范围</h3><p className="mt-2 text-xs leading-6 text-gray-500">原需求、商品方案、后续处理与关系控制。结果来自本地预设，没有查询真实平台或 WhatsApp。</p></div></div><p className="text-xs leading-6 text-gray-500">接续仅归并合作事项，不发送消息，不解除人工接管或暂停。重复打开同一历史需求会复用已关联事项。</p><div className="flex flex-wrap justify-end gap-3 border-t border-gray-100 pt-5 dark:border-gray-800"><Button variant="outline" onClick={() => setHistoryDetail(null)}>关闭</Button>{!detail.checked && <Button disabled={!hydrated} onClick={() => checkHistory(detail)}>模拟核实当前事实</Button>}{detail.checked && detail.status === "actionable" && <Button disabled={!hydrated} onClick={() => resumeHistory(detail)}>{resumeLabel}<Icon name="arrow" /></Button>}</div></div>;
        })()}
      </Dialog>
      <Dialog open={!!reference} onClose={() => setReferenceId(null)} title={reference?.title || "参考案例"} description="示例帮助校订处理方式；不是现行商业政策，也不会触发训练或外部操作。">
        {reference && <div className="space-y-5"><MarketPill market={reference.market} /><div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800/60"><p lang={MARKETS[reference.market].locale} className="text-sm leading-7 text-gray-700 dark:text-gray-200">{reference.original}</p><p className="mt-2 text-xs leading-6 text-gray-500">中文理解：{reference.translation}</p></div><div><h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">认可的处理方式</h3><p className="mt-2 text-sm leading-7 text-gray-600 dark:text-gray-300">{reference.response}</p></div><div><h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">处理前需要的事实</h3><ul className="mt-2 space-y-2">{reference.facts.map((fact) => <li key={fact} className="flex gap-2 text-sm leading-6 text-gray-500"><Icon name="check" className="mt-0.5 size-4 shrink-0 text-brand-500" />{fact}</li>)}</ul></div><Notice>{reference.avoid}</Notice><div className="flex justify-end gap-3"><Button variant="outline" onClick={() => setReferenceId(null)}>关闭</Button><Button onClick={() => { setReferenceId(null); setTab("evaluation"); }}>查看离线回放<Icon name="arrow" /></Button></div></div>}
      </Dialog>
    </div>
  );
}
