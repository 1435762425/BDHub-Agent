// Deterministic, browser-only prototype data. No platform credentials or real creators.
export type Market = "mx" | "br" | "it";
export type View = "overview" | "goals" | "opportunities" | "workspace" | "results" | "agents" | "settings";
export type CaseStatus = "processing" | "waiting_creator" | "waiting_business" | "needs_operator" | "closed";
export const MARKETS: Record<Market,{name:string;locale:string;currency:string}> = {
  mx:{name:"墨西哥",locale:"es-MX",currency:"MXN"},br:{name:"巴西",locale:"pt-BR",currency:"BRL"},it:{name:"意大利",locale:"it-IT",currency:"EUR"},
};
export const STATUS: Record<CaseStatus,string> = {processing:"处理中",waiting_creator:"等达人",waiting_business:"等业务结果",needs_operator:"需要决定",closed:"已结束"};
export interface Creator {id:string;name:string;handle:string;market:Market;category:string;avatar:string;format:string;profileFresh:boolean;}
export interface Product {id:string;name:string;market:Market;category:string;image:string;price:number;commission:number;sample:boolean;ready:boolean;}
export interface Relation {creatorId:string;control:"ai"|"human"|"paused";revision:number;preferences:string[];marketingStopped:boolean;}
export interface Opportunity {id:string;creatorId:string;productId:string;source:"first"|"second";evidence:string;readiness:"ready"|"needs_facts"|"duplicate"|"excluded";}
export interface Message {id:string;direction:"creator"|"team"|"system";text:string;translation?:string;by:"creator"|"ai"|"human"|"system";at:string;receipt?:"accepted"|"unknown";}
export interface CooperationCase {id:string;creatorId:string;productId:string;goalId:string;title:string;status:CaseStatus;nextStep:string;reason:string;revision:number;delivered:boolean;adopted:boolean;sendState:"not_sent"|"accepted"|"unknown";draftValid:boolean;serviceRequested?:boolean;messages:Message[];}
export interface Goal {id:string;name:string;description:string;markets:Market[];priority:"second"|"balanced"|"first";status:"active"|"paused"|"draft";dailyBudget:number;version:number;pauseScope?:"all_outbound"|"new_acquisition";}
export interface HistoryItem {id:string;creatorId:string;productId:string;question:string;original:string;date:string;status:"resolved"|"expired"|"actionable"|"unknown";checked:boolean;resolution:"resolved"|"expired"|"actionable";caseId?:string;}
export interface AgentConfig {instructions:string;version:number;skills:string[];tone:"friendly"|"direct"|"patient";messageMode:"fixed"|"skeleton"|"generated";messageRule:string;}
export interface DemoSettings {modelBudget:number;operatorMinutes:number;contactCooldown:number;autoFollowup:boolean;}
export interface Activity {id:string;title:string;detail:string;at:string;}
export interface DemoState {version:1;marketFilter:"all"|Market;creators:Creator[];products:Product[];relations:Relation[];opportunities:Opportunity[];cases:CooperationCase[];goals:Goal[];histories:HistoryItem[];agent:AgentConfig;settings:DemoSettings;activities:Activity[];notificationRead:boolean;}
export type Scenario = "received_item_request"|"sample_requested"|"ask_later"|"adopted"|"opt_out"|"send_unknown"|"verify_accepted";
export type DemoAction =
 | {type:"hydrate";state:DemoState}|{type:"reset"}|{type:"market";market:"all"|Market}
 | {type:"create_goal";goal:Omit<Goal,"id"|"version">}
 | {type:"update_goal";id:string;patch:Partial<Omit<Goal,"id">>}
 | {type:"activate_opportunity";id:string}|{type:"refresh_opportunity";id:string}|{type:"exclude_opportunity";id:string}
 | {type:"control";creatorId:string;mode:Relation["control"]}
 | {type:"human_message";caseId:string;text:string}
 | {type:"add_note";caseId:string;text:string}
 | {type:"simulate_event";caseId:string;event:Scenario}
 | {type:"advance_case";caseId:string}
 | {type:"resolve_decision";caseId:string;choice:"keep"|"pause"|"manual";note:string}
 | {type:"check_history";id:string}|{type:"resume_history";id:string}
 | {type:"save_agent";patch:Partial<AgentConfig>}|{type:"save_settings";patch:Partial<DemoSettings>}
 | {type:"read_notifications"};

const message=(id:string,text:string,translation:string):Message=>({id,direction:"creator",by:"creator",text,translation,at:"10:24"});
export function initialState():DemoState {
 const creators:Creator[]=[
  {id:"sofia",name:"Sofía Luna",handle:"sofia.demo",market:"mx",category:"数码好物",avatar:"/images/user/user-17.jpg",format:"短视频",profileFresh:true},
  {id:"luca",name:"Luca Moretti",handle:"luca.demo",market:"it",category:"数码生活",avatar:"/images/user/user-18.jpg",format:"短视频",profileFresh:true},
  {id:"marina",name:"Marina Costa",handle:"marina.demo",market:"br",category:"生活方式",avatar:"/images/user/user-19.jpg",format:"直播 / 短视频",profileFresh:true},
  {id:"camila",name:"Camila Reyes",handle:"camila.demo",market:"mx",category:"数码好物",avatar:"/images/user/user-20.jpg",format:"短视频",profileFresh:true},
  {id:"giulia",name:"Giulia Rossi",handle:"giulia.demo",market:"it",category:"创意生活",avatar:"/images/user/user-21.jpg",format:"短视频",profileFresh:false},
  {id:"pedro",name:"Pedro Lima",handle:"pedro.demo",market:"br",category:"数码测评",avatar:"/images/user/user-22.jpg",format:"直播",profileFresh:true},
  {id:"valeria",name:"Valeria Ruiz",handle:"valeria.demo",market:"mx",category:"数码生活",avatar:"/images/user/user-23.jpg",format:"短视频",profileFresh:false},
  {id:"andrea",name:"Andrea Bianchi",handle:"andrea.demo",market:"it",category:"数码测评",avatar:"/images/user/user-24.jpg",format:"短视频",profileFresh:true},
 ];
 const products:Product[]=[
  {id:"watch-mx",name:"运动智能手表",market:"mx",category:"数码好物",image:"/images/product/product-02.jpg",price:2499,commission:12,sample:true,ready:true},
  {id:"buds-br",name:"无线降噪耳机",market:"br",category:"生活方式",image:"/images/product/product-05.jpg",price:399,commission:10,sample:true,ready:true},
  {id:"tablet-it",name:"轻便平板电脑",market:"it",category:"数码生活",image:"/images/product/product-04.jpg",price:299,commission:8,sample:true,ready:true},
  {id:"phone-mx",name:"便携影像手机",market:"mx",category:"数码生活",image:"/images/product/product-03.jpg",price:5999,commission:9,sample:false,ready:true},
  {id:"laptop-it",name:"轻薄办公笔记本",market:"it",category:"数码测评",image:"/images/product/product-01.jpg",price:899,commission:6,sample:false,ready:false},
  {id:"watch-br",name:"运动智能手表",market:"br",category:"数码测评",image:"/images/product/product-02.jpg",price:599,commission:11,sample:true,ready:true},
 ];
 const goals:Goal[]=[
  {id:"g-second",name:"三市场二发持续经营",description:"优先查找已有同款带货记录的达人，合并重复机会并接续回复。",markets:["mx","br","it"],priority:"second",status:"active",dailyBudget:20,version:2},
  {id:"g-first",name:"精选数码商品一发匹配",description:"从有样品路径的商品中匹配适合的达人，以事实组织合作邀约。",markets:["mx","br"],priority:"first",status:"active",dailyBudget:10,version:1},
  {id:"g-history",name:"墨西哥历史回复接续",description:"先核对当前事实，再推进仍然有效的未结事项。",markets:["mx"],priority:"balanced",status:"draft",dailyBudget:8,version:1},
 ];
 const opps:Opportunity[]=[
  {id:"o1",creatorId:"sofia",productId:"watch-mx",source:"second",evidence:"近14天有同款带货记录，且已明确持有实物。",readiness:"ready"},
  {id:"o2",creatorId:"luca",productId:"tablet-it",source:"second",evidence:"近期推广过同款；当前合作方案可用。",readiness:"ready"},
  {id:"o3",creatorId:"marina",productId:"buds-br",source:"first",evidence:"价格带与近期生活方式内容相符，可查询样品路径。",readiness:"ready"},
  {id:"o4",creatorId:"camila",productId:"watch-mx",source:"first",evidence:"近期类目与商品相符，历史回复偏好简短说明。",readiness:"ready"},
  {id:"o5",creatorId:"giulia",productId:"tablet-it",source:"second",evidence:"历史有相关带货记录，近期画像需要更新。",readiness:"needs_facts"},
  {id:"o6",creatorId:"pedro",productId:"watch-br",source:"second",evidence:"直播中有同类商品表现，本轮尚未联系。",readiness:"ready"},
  {id:"o7",creatorId:"valeria",productId:"phone-mx",source:"second",evidence:"历史推广记录命中，当前关键资料待核实。",readiness:"needs_facts"},
  {id:"o8",creatorId:"andrea",productId:"laptop-it",source:"second",evidence:"内容形式匹配，商品方案仍需准备。",readiness:"needs_facts"},
  {id:"o9",creatorId:"sofia",productId:"watch-mx",source:"first",evidence:"一发也命中同一达人与商品，统一到既有合作。",readiness:"duplicate"},
  {id:"o10",creatorId:"pedro",productId:"buds-br",source:"first",evidence:"数码类目和价格带接近，可进入本次候选。",readiness:"ready"},
  {id:"o11",creatorId:"camila",productId:"phone-mx",source:"second",evidence:"已找到相关商品证据，先处理现有未结需求。",readiness:"ready"},
  {id:"o12",creatorId:"andrea",productId:"tablet-it",source:"first",evidence:"价格带与近期内容形式匹配。",readiness:"ready"},
 ];
 const cases:CooperationCase[]=[
  {id:"case-sofia-watch-mx",creatorId:"sofia",productId:"watch-mx",goalId:"g-second",title:"已有实物，索取商品卡",status:"processing",nextStep:"核对同款并交付有效商品卡",reason:"达人明确表示已有实物，无需重复申请样品。",revision:2,delivered:false,adopted:false,sendState:"not_sent",draftValid:true,messages:[message("m1","Hola, ya tengo este reloj. ¿Me compartes el enlace?","我已经有这款手表，可以给我推广链接吗？")]},
  {id:"case-luca-tablet-it",creatorId:"luca",productId:"tablet-it",goalId:"g-second",title:"希望调整合作佣金",status:"needs_operator",nextStep:"确认是否沿用当前条件",reason:"提出了现有规则之外的商业条件，需要一次具体决定。",revision:1,delivered:false,adopted:false,sendState:"not_sent",draftValid:true,messages:[message("m2","Possiamo valutare una commissione diversa?","可以考虑其他佣金条件吗？")]},
  {id:"case-marina-buds-br",creatorId:"marina",productId:"buds-br",goalId:"g-first",title:"查询样品审核进度",status:"waiting_business",nextStep:"等待平台状态更新后重新核验",reason:"已查到待审核记录，正常等待不需要人工批准。",revision:1,delivered:true,adopted:false,sendState:"accepted",draftValid:false,messages:[message("m3","Já solicitei a amostra. Tem alguma novidade?","我已经申请样品了，有进展吗？"),{id:"m4",direction:"team",by:"ai",text:"Seu pedido está em análise. Avisaremos quando houver uma atualização confirmada.",translation:"申请目前待审核，有已确认的新状态时会继续告知。",at:"10:20",receipt:"accepted"}]},
  {id:"case-pedro-watch-br",creatorId:"pedro",productId:"watch-br",goalId:"g-second",title:"商品卡结果待核验",status:"waiting_business",nextStep:"只读核验原发送意图，不重发",reason:"传输超时不能判断是否已提交成功。",revision:1,delivered:false,adopted:false,sendState:"unknown",draftValid:false,messages:[message("m5","Pode enviar a ficha do produto?","可以发给我商品卡吗？"),{id:"m6",direction:"system",by:"system",text:"商品卡结果未知，已暂停依赖动作，等待核验。",at:"09:46"}]},
 ];
 const histories:HistoryItem[]=[
  {id:"h1",creatorId:"camila",productId:"watch-mx",question:"加了商品，却找不到样品入口",original:"Agregué el producto, pero no veo la opción de muestra.",date:"9月8日",status:"unknown",checked:false,resolution:"actionable"},
  {id:"h2",creatorId:"sofia",productId:"watch-mx",question:"已有实物，想确认对应商品卡",original:"Ya tengo el reloj, ¿me das la ficha?",date:"9月9日",status:"actionable",checked:true,resolution:"actionable"},
  {id:"h3",creatorId:"valeria",productId:"phone-mx",question:"之前活动结束，是否还可申请",original:"¿La campaña anterior sigue disponible?",date:"9月7日",status:"expired",checked:true,resolution:"expired"},
  {id:"h4",creatorId:"camila",productId:"phone-mx",question:"询问的入口已在后续对话交付",original:"Gracias, ya encontré el enlace.",date:"9月6日",status:"resolved",checked:true,resolution:"resolved"},
 ];
 cases.forEach(c=>{c.serviceRequested=true;});
 return {version:1,marketFilter:"all",creators,products,opportunities:opps,cases,goals,histories,
  relations:creators.map(c=>({creatorId:c.id,control:"ai",revision:1,marketingStopped:false,preferences:["喜欢简短、明确的合作说明","其他商品机会统一协调"]})),
  agent:{instructions:"负责当前达人的持续合作。先处理明确诉求和未完成承诺，再安排新的商品机会。只基于有效事实回复，超出商业条件交给运营。",version:1,skills:["existing-item","invitation","sample-status"],tone:"friendly",messageMode:"generated",messageRule:"不重复追问已知信息；价格、佣金和链接只引用当前有效方案。"},
  settings:{modelBudget:20,operatorMinutes:120,contactCooldown:72,autoFollowup:false},notificationRead:false,
  activities:[{id:"a1",title:"本轮已合并重复机会",detail:"Sofía 的一发与二发命中进入同一关系。",at:"10:28"},{id:"a2",title:"一项商业决定等待处理",detail:"Luca 希望调整合作条件。",at:"10:24"},{id:"a3",title:"历史问题已整理为候选",detail:"先核实当前事实，不自动补发旧消息。",at:"10:12"}],
 };
}

export function matchesMarket(state:DemoState,market:Market){return state.marketFilter==="all"||state.marketFilter===market;}
export const creatorFor=(state:DemoState,id:string)=>state.creators.find(c=>c.id===id)!;
export const productFor=(state:DemoState,id:string)=>state.products.find(p=>p.id===id)!;
export const relationFor=(state:DemoState,id:string)=>state.relations.find(r=>r.creatorId===id)!;
export const activeCaseFor=(state:DemoState,creatorId:string,productId:string)=>state.cases.find(c=>c.creatorId===creatorId&&c.productId===productId&&c.status!=="closed");
export const money=(p:Product)=>new Intl.NumberFormat(MARKETS[p.market].locale,{style:"currency",currency:MARKETS[p.market].currency,maximumFractionDigits:0}).format(p.price);

function log(s:DemoState,title:string,detail:string){s.activities.unshift({id:`a-${s.activities.length+1}-${Date.now()}`,title,detail,at:new Date().toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"})});s.activities=s.activities.slice(0,50);s.notificationRead=false;}
function addMessage(c:CooperationCase,text:string,by:Message["by"],translation?:string){c.messages.push({id:`m-${Date.now()}-${c.messages.length}`,direction:by==="creator"?"creator":by==="system"?"system":"team",by,text,translation,at:"刚刚",...((by==="ai"||by==="human")?{receipt:"accepted" as const}:{})});}
function ensureCase(s:DemoState,creatorId:string,productId:string,source:"first"|"second"|"history"){
 const prior=s.cases.find(c=>c.creatorId===creatorId&&c.productId===productId);if(prior)return prior;
 const product=productFor(s,productId);const creator=creatorFor(s,creatorId);
 const available=s.goals.filter(g=>g.markets.includes(creator.market)&&g.status==="active");
 const goal=available.find(g=>g.priority===source)||available[0]||s.goals.find(g=>g.markets.includes(creator.market));
 if(!goal)return undefined;
 const r=relationFor(s,creatorId);
 const c:CooperationCase={id:`case-${creatorId}-${productId}-${s.cases.length}`,creatorId,productId,goalId:goal.id,title:source==="history"?"接续已核实历史需求":`${source==="first"?"一发":"二发"}商品合作`,status:r.control==="human"?"needs_operator":"processing",nextStep:"根据当前事实准备个性化消息",reason:`已关联${product.name}与当前关系，尚未发送。`,revision:1,delivered:false,adopted:false,sendState:"not_sent",draftValid:r.control==="ai",serviceRequested:source==="history",messages:[]};s.cases.unshift(c);return c;
}
function setControl(s:DemoState,creatorId:string,mode:Relation["control"]){
 const r=relationFor(s,creatorId);r.control=mode;r.revision++;
 s.cases.filter(c=>c.creatorId===creatorId).forEach(c=>{c.revision++;c.draftValid=mode==="ai"&&c.status==="processing"&&c.sendState!=="unknown"&&!c.delivered&&(!r.marketingStopped||!!c.serviceRequested);});
}
const LOCAL_MESSAGES={
 mx:{item:"Ya tengo el producto. ¿Me compartes la ficha?",sample:"Ya solicité la muestra. ¿Cuál es el siguiente paso?",later:"¿Podemos hablar la próxima semana?",stop:"Por favor, no me envíen más propuestas.",sampleAck:"Tu solicitud está pendiente de revisión. Te avisaremos cuando haya un cambio confirmado.",laterAck:"Claro, retomaremos la conversación la próxima semana."},
 br:{item:"Já tenho o produto. Pode compartilhar a ficha?",sample:"Já solicitei a amostra. Qual é o próximo passo?",later:"Podemos conversar na próxima semana?",stop:"Por favor, não me enviem mais propostas.",sampleAck:"Seu pedido está em análise. Avisaremos quando houver uma atualização confirmada.",laterAck:"Claro, retomaremos a conversa na próxima semana."},
 it:{item:"Ho già il prodotto. Puoi mandarmi la scheda?",sample:"Ho richiesto il campione. Qual è il prossimo passo?",later:"Possiamo parlarne la prossima settimana?",stop:"Per favore, non inviatemi altre proposte.",sampleAck:"La richiesta è in attesa di verifica. Ti aggiorneremo quando avremo informazioni confermate.",laterAck:"Certo, riprenderemo la conversazione la prossima settimana."}
};
export function demoReducer(state:DemoState,action:DemoAction):DemoState {
 if(action.type==="hydrate")return action.state;
 if(action.type==="reset")return initialState();
 const s=structuredClone(state);
 if(action.type==="market"){s.marketFilter=action.market;return s;}
 if(action.type==="read_notifications"){s.notificationRead=true;return s;}
 if(action.type==="create_goal"){s.goals.unshift({...action.goal,id:`g-${Date.now()}`,version:1});log(s,"经营目标已保存",`${action.goal.name} · 原型演示，无真实外发`);return s;}
 if(action.type==="update_goal"){const g=s.goals.find(x=>x.id===action.id);if(g){Object.assign(g,action.patch);g.version++;log(s,"经营策略已更新",`${g.name} · v${g.version}`);}return s;}
 if(action.type==="save_agent"){s.agent={...s.agent,...action.patch,version:s.agent.version+1};log(s,"Agent 配置已保存",`原型配置 v${s.agent.version}，没有调用模型服务。`);return s;}
 if(action.type==="save_settings"){s.settings={...s.settings,...action.patch};log(s,"资源设置已保存","只影响当前浏览器的演示状态。");return s;}
 if(action.type==="control"){
  setControl(s,action.creatorId,action.mode);
  s.cases.filter(c=>c.creatorId===action.creatorId).forEach(c=>{addMessage(c,action.mode==="human"?"运营已接管，尚未提交的 AI 草稿已作废。":action.mode==="paused"?"该关系的自动外联已暂停。":"已交还 Agent，将从最新事实重新判断；不会解除既有营销拒联。","system");});
  log(s,action.mode==="ai"?"关系已交还 Agent":"关系控制已更新",creatorFor(s,action.creatorId).name);return s;
 }
 if(action.type==="activate_opportunity"||action.type==="refresh_opportunity"||action.type==="exclude_opportunity"){
  const o=s.opportunities.find(x=>x.id===action.id);if(!o)return state;
  if(action.type==="refresh_opportunity"){o.readiness=productFor(s,o.productId).ready?"ready":"needs_facts";creatorFor(s,o.creatorId).profileFresh=true;log(s,"关键资料已模拟复核",`${creatorFor(s,o.creatorId).name}：${o.readiness==="ready"?"可进入准备":"商品方案仍待准备"}`);}
  if(action.type==="exclude_opportunity"){o.readiness="excluded";log(s,"已排除本轮机会","未改变整段关系或其他商品机会。");}
  if(action.type==="activate_opportunity"&&(o.readiness==="ready"||o.readiness==="duplicate")){
   const relationship=relationFor(s,o.creatorId);if(relationship.control==="paused"||relationship.marketingStopped)return state;
   const existing=s.cases.find(c=>c.creatorId===o.creatorId&&c.productId===o.productId);ensureCase(s,o.creatorId,o.productId,o.source);o.readiness="duplicate";log(s,existing?"已关联既有合作":"合作事项已准备",`${creatorFor(s,o.creatorId).name} · 不会重复建立同款事项。`);
  }return s;
 }
 if(action.type==="check_history"||action.type==="resume_history"){
  const h=s.histories.find(x=>x.id===action.id);if(!h)return state;
  if(action.type==="check_history"){h.checked=true;h.status=h.resolution;log(s,"历史事项已模拟核实",h.question);}
  else if(h.checked&&h.status==="actionable"){
   const c=ensureCase(s,h.creatorId,h.productId,"history");if(!c)return state;h.caseId=c.id;c.reason=h.question;c.nextStep="基于今天的事实接续需求，不补发旧模板";log(s,"已接续历史事项",`${creatorFor(s,h.creatorId).name} · 尚未发送新消息`);
  }return s;
 }
 const c="caseId" in action?s.cases.find(x=>x.id===action.caseId):undefined;if(!c)return state;
 const r=relationFor(s,c.creatorId);const creator=creatorFor(s,c.creatorId);
 if(action.type==="add_note"){if(!action.text.trim())return state;addMessage(c,`运营补充（内部）：${action.text.trim()}`,"system");c.revision++;log(s,"内部信息已补充",creator.name);return s;}
 if(action.type==="human_message"){
  if(r.control!=="human"||!action.text.trim())return state;
  addMessage(c,action.text.trim(),"human");c.status="waiting_creator";c.revision++;c.draftValid=false;log(s,"人工消息已演示提交",`${creator.name} · 仅保存在浏览器`);return s;
 }
 if(action.type==="resolve_decision"){
  c.revision++;if(action.choice==="manual"){setControl(s,c.creatorId,"human");c.nextStep="由运营继续处理";}
  else if(action.choice==="pause"){c.status="closed";c.draftValid=false;c.nextStep="本轮暂缓，保留达人关系";}
  else{c.status="processing";c.nextStep="沿用现有条件继续沟通";setControl(s,c.creatorId,"ai");}
  addMessage(c,`运营决定：${action.choice==="keep"?"沿用当前条件":action.choice==="pause"?"暂缓本次合作":"人工接管"}${action.note?`。${action.note}`:""}`,"system");log(s,"具体商业决定已记录",creator.name);return s;
 }
 if(action.type==="advance_case"){
  const goal=s.goals.find(g=>g.id===c.goalId);
  if(r.control!=="ai"||c.sendState==="unknown"||c.status!=="processing"||!c.draftValid||c.delivered||(r.marketingStopped&&!c.serviceRequested)||!goal||!goal.markets.includes(creator.market)||(goal.status!=="active"&&!(goal.status==="paused"&&goal.pauseScope==="new_acquisition"&&c.serviceRequested)))return state;
  const text=creator.market==="mx"?"¡Hola! Aquí tienes la propuesta para revisar las condiciones de colaboración.":creator.market==="br"?"Olá! Aqui está a proposta para você consultar as condições de parceria.":"Ciao! Ecco la proposta per consultare le condizioni di collaborazione.";
  addMessage(c,text,"ai","这是对应商品的合作方案，请查看当前条件。");c.sendState="accepted";c.delivered=true;c.draftValid=false;c.status="waiting_creator";c.nextStep="等待达人确认采用，暂不重复催促";c.revision++;log(s,"已演示交付合作方案",`${creator.name} · 平台接收不等于已采用`);return s;
 }
 if(action.type==="simulate_event"){
  const words=LOCAL_MESSAGES[creator.market];
  c.revision++;c.draftValid=false;
  if(action.event==="send_unknown"){c.sendState="unknown";c.status="waiting_business";c.nextStep="先核验原发送结果，不重发";addMessage(c,"演示事件：传输结果未知，停止重复提交并进入只读核验。","system");}
  else if(action.event==="verify_accepted"){
   if(c.sendState!=="unknown")return state;c.sendState="accepted";c.delivered=true;c.status="waiting_creator";c.nextStep="原意图已模拟核验接收，等待达人确认";addMessage(c,"演示平台证据：原意图已被接收，没有再次发送。","system");
  }else if(action.event==="adopted"){
   if(!c.delivered||c.sendState==="unknown")return state;c.adopted=true;c.status="closed";c.nextStep="方案采用已演示核实，收益仍需独立证据";addMessage(c,"演示业务证据：达人已采用具体方案。","system");
  }else if(action.event==="opt_out"){
   r.control="paused";r.marketingStopped=true;r.revision++;r.preferences.push("明确停止营销联系（演示）");s.cases.filter(x=>x.creatorId===c.creatorId).forEach(x=>{x.draftValid=false;x.serviceRequested=false;if(x.status!=="closed"){x.status="closed";x.nextStep="已停止本轮营销，保留主动服务入口";}});addMessage(c,words.stop,"creator","请不要再发营销合作邀请。");
  }else if(action.event==="ask_later"){
   addMessage(c,words.later,"creator","可以下周再聊吗？");if(r.control==="ai")addMessage(c,words.laterAck,"ai","好的，下周再接续沟通。");c.status="waiting_creator";c.nextStep="已记录下周重新核实，不再发送原草稿";
  }else if(action.event==="sample_requested"){
   c.serviceRequested=true;addMessage(c,words.sample,"creator","我已经申请样品，下一步是什么？");if(r.control==="ai")addMessage(c,words.sampleAck,"ai","申请目前待审核，有已确认状态变化时再告知。");c.status=r.control==="ai"?"waiting_business":"needs_operator";c.nextStep="查询当前申请状态，等待已核实的业务变化";
  }else{
   c.serviceRequested=true;addMessage(c,words.item,"creator","我已有商品，可以给我对应商品卡吗？");c.status=r.control==="ai"?"processing":"needs_operator";c.nextStep="已有实物，核对同款后交付方案";c.draftValid=r.control==="ai"&&c.sendState!=="unknown";
  }log(s,"已模拟一条新事件",`${creator.name} · 旧草稿已重新判断`);return s;
 }
 return s;
}
