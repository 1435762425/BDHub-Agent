export type DemoSource="selected"|"campaign";
export type ScenarioKey="clean"|"unrated"|"legacy_only"|"missing_link"|"product_invalid"|"reply_open";

export interface DemoStage{
 key:string;index:string;title:string;unit:string;input:string;output:string;rule:string;failure:string;refresh:string;
}

export interface DemoScenario{
 key:ScenarioKey;label:string;description:string;source:DemoSource;sales:number;rating:number|null;
 totalPercent:number;publicPercent:number;creatorPercent:number;campaignDays:number|null;stock:number|null;
 offerReady:boolean;linkState:"verified"|"missing"|"legacy_only";identityReady:boolean;replyOpen:boolean;
 cardPercent?:number;joinedCampaignDays?:number;
}

export interface DemoGateResult{key:string;label:string;state:"pass"|"stop"|"wait";detail:string;}

export const BUSINESS_PHASES=[
 {key:"product",index:"01",title:"商品准备",question:"这个 PID 现在能用吗？",count:2140,unit:"个 PID 已备好",summary:"商品合格、方案完整，并且最后成功快照中有可用 TapLink。",steps:["采集并筛出合格 PID","选定唯一当前方案","复用或创建 TapLink"]},
 {key:"creator",index:"02",title:"达人准备",question:"这个达人是真实可联系的人吗？",count:2930,unit:"个达人已识别",summary:"同 PID 正销量线索已经取得，并解析到稳定 OECID。",steps:["按 PID 查询正销量达人","handle 去重判定一次","绑定市场 × OECID"]},
 {key:"send",index:"03",title:"发送安排",question:"这一条现在可以进入发送批次吗？",count:1880,unit:"条当前可发送",summary:"达人关系清晰、没有冷却或未结问题，并冻结精确材料。",steps:["生成达人 × PID 位置","每达人选择一个最优商品","组批并冻结 Offer + listId"]},
] as const;

export const PID_SNAPSHOT={
 observedAt:"2026-09-19",
 fullManaged:{collected:10000,currentEligible:2291,currentRejected:7709,selectedPool:3450,
  links:{ready:1098,reuse:1193,reading:1154,missing:4,review:1}},
 campaign:{uniquePids:2697,chosen:449,held:2248,newlyJoined:18,
  linkRows:{ready:420,reuseRows:176,reusePids:152,missing:259,readIncomplete:2,review:6}},
 linkIntents:{verified:1519},
 inventory:{lists:2067,members:2067},
 historicalCleanup:{scanned:1908,valid:1786,invalid:122,verifiedDeletes:122},
} as const;

export const FLOW_STAGES:DemoStage[]=[
 {key:"collect",index:"01",title:"PID 采集",unit:"商品 PID",input:"全托高机会 / Campaign 活动",output:"来源快照 + PID 去重",rule:"每条 PID 保留来源、活动、事实时间和版本。",failure:"读取中断保留游标，不生成半份生效快照。",refresh:"手动主动采集；定时为可选开关。"},
 {key:"screen",index:"02",title:"商品筛选",unit:"PID / Offer",input:"当前来源快照",output:"合格商品方案",rule:"全托销量≥300；有评分≥4.0，无评分允许；佣金差≥2点。Campaign 使用独立期限与库存规则。",failure:"进入不合格层，历史事实保留。",refresh:"采集完成、规则变化或商品事实变化时重算。"},
 {key:"offer",index:"03",title:"精确 Offer",unit:"PID × 活动",input:"合格候选",output:"唯一当前方案",rule:"达人佣金最高 → 截止更晚 → 活动 ID 定序；不同 Offer 不拼字段。",failure:"没有完整方案则等待事实，不进入建链。",refresh:"每次筛分与发送批次冻结前。"},
 {key:"link",index:"04",title:"TapLink 材料",unit:"PID × 方案",input:"精确 Offer",output:"标准 listId",rule:"只认统一分佣与命名规则创建的标准卡；历史卡忽略。没有标准卡就补建。",failure:"标准链接缺失或创建结果未知时等待，不进入达人查询。",refresh:"Campaign 每日随来源核验；全托已选每周核验一次。"},
 {key:"leads",index:"05",title:"PID 查达人",unit:"PID 查询任务",input:"material-ready PID",output:"正销量达人线索",rule:"首次 PID 优先；已查 PID 7 天后刷新；失败不写 queried_at。",failure:"额度耗尽保留断点；无链接 PID 不进入查询。",refresh:"首次一次；完成后每 7 天到期。"},
 {key:"identity",index:"06",title:"OECID 身份",unit:"去重 handle",input:"达人线索",output:"稳定达人身份",rule:"同一 handle 只做一次 Find 判定；找到后以市场×OECID 归一。",failure:"明确搜索不到则保留证据，但不进入位置。",refresh:"Find 判定一次；画像按需或 48 小时刷新。"},
 {key:"position",index:"07",title:"达人×PID 位置",unit:"达人 × PID",input:"稳定达人 + 同品线索",output:"可经营位置",rule:"一个达人可保留多个 PID；渠道属于商品，冷却和拒联属于达人。",failure:"商品失效、缺材料、关系阻断分别分层，不丢线索。",refresh:"页面读取时实时重算，不保存静态排序。"},
 {key:"pool",index:"08",title:"严格发送池",unit:"当前最优位置",input:"material-ready 位置",output:"ready / queued / cooling / reply",rule:"达人之间按线索强度；达人内部按佣金→rank→全托→PID。",failure:"任一门失败退出 ready，并显示具名原因。",refresh:"实时重算；正式组批后冻结具体 Offer 和 listId。"},
];

export const REFRESH_RULES=[
 {object:"全托商品源",mode:"手动主动采集",optional:"可选定时",cycle:"默认不开；运营按需启动",effect:"生成完整新快照，旧完整快照在中断时继续生效"},
 {object:"Campaign 商品 + TapLink",mode:"来源完整刷新并核验链接",optional:"手动 + 可选定时",cycle:"每日一次",effect:"同一轮更新活动、商品和对应链接；确认失效则清理"},
 {object:"商品筛选",mode:"确定性重算",optional:"自动",cycle:"新快照或规则版本变化",effect:"只改变当前资格，不删除历史线索"},
 {object:"新建 TapLink",mode:"创建后回读一次",optional:"写入结果结算",cycle:"每次新建后立即",effect:"取得确定 listId；之后不再为这个动作反复核验"},
 {object:"全托已选 TapLink",mode:"统一核验",optional:"确认周期，尚未启用",cycle:"每周一次",effect:"以上次成功结果为准；确认失效则清理"},
 {object:"Kalodata 线索",mode:"到期队列",optional:"手动启动",cycle:"首次一次，之后 7 天",effect:"未到期 PID 不为凑数量重复查询"},
 {object:"OECID",mode:"达人级判定",optional:"手动启动",cycle:"Find 一次；画像按需/48h",effect:"搜索不到不自动重试，不伪造身份"},
 {object:"发送池",mode:"读时重算",optional:"无后台轮询",cycle:"每次读取",effect:"时间、关系、商品与材料变化即时改变分层"},
 {object:"正式批次",mode:"冻结快照",optional:"用户启动",cycle:"每个批次一次",effect:"冻结 source/campaign/rate/listId/规则版本"},
];

export const PID_REFRESH_CLOCKS=[
 {key:"campaign",title:"Campaign 商品",cadence:"每日",items:["刷新 Campaign 来源时顺便核验对应 TapLink","确认失效的链接直接进入清理"]},
 {key:"selected",title:"全托已选商品",cadence:"每周",items:["统一核验一次 TapLink 状态","确认失效则清理；其余继续沿用"]},
] as const;

export const TAPLINK_VALIDITY_CHECKS=[
 {label:"统一规则",detail:"分佣版本 commission-1-to-2-v1；命名版本 link-naming-v1。"},
 {label:"统一名称",detail:"🔥 BJN {short_name} {creator_percent}% {tail}。"},
 {label:"精确绑定",detail:"标准 listId 精确绑定 PID、来源、Campaign 和当前分佣。"},
 {label:"唯一发送卡",detail:"发送池只读取当前标准 listId，不从历史卡临时挑选。"},
 {label:"历史卡",detail:"全部忽略；确认失效才清理，仍可用的只保留为历史。"},
] as const;

export const TAPLINK_PERFORMANCE=[
 {label:"单 PID 严格核验",value:"约 2 秒",detail:"典型需要列表搜索 + 成员读取 2 次平台请求；顺序 1 QPS 的 5 PID 实测约 10 秒量级。"},
 {label:"1,908 张库存扫描",value:"约 8 分 18 秒",detail:"完整库存历史实测 497.74 秒；适合一次扫描多人复用，不适合每个 PID 重做。"},
] as const;

export const LOCKS=[
 {name:"来源快照锁",scope:"PID 快照",when:"采集完成切换 head",protects:"中断页、重复页或数量不一致不能冒充完整货盘"},
 {name:"方案绑定锁",scope:"PID × Campaign",when:"筛选与冻结",protects:"佣金、期限、库存和活动必须来自同一个 Offer"},
 {name:"建链意图锁",scope:"PID × 方案 × 规则版本",when:"平台写入前",protects:"重启、超时和重复点击不重复建链"},
 {name:"账号 lease / QPS",scope:"ACC9 / ACC6",when:"远端请求前",protects:"同一账号不会被多个 worker 叠加速率或抢占身份"},
 {name:"PID 查询锁",scope:"PID × 查询窗口",when:"Kalodata claim",protects:"同一 PID 不重复消费日额度，失败保留原队列位置"},
 {name:"身份判定锁",scope:"市场 × handle",when:"Find claim",protects:"同一达人只判一次；blocked 重试与明确未找到分开"},
 {name:"关系控制锁",scope:"市场 × OECID",when:"池子重算",protects:"拒联、人工接管、未结回复会阻断该达人所有商品"},
 {name:"批次冻结锁",scope:"达人 × PID × Offer × listId",when:"用户开始批次",protects:"页面看到的样例、佣金和真正发送的材料完全一致"},
 {name:"发送 fence",scope:"发送意图 / component",when:"平台调用",protects:"过期 worker 不能提交迟到结果；unknown 禁止自动重发"},
];

export const SCENARIOS:DemoScenario[]=[
 {key:"clean",label:"正常全托 PID",description:"商品、方案、链接和关系全部就绪。",source:"selected",sales:1280,rating:4.7,totalPercent:16,publicPercent:11,creatorPercent:14,campaignDays:null,stock:null,offerReady:true,linkState:"verified",identityReady:true,replyOpen:false,cardPercent:14},
 {key:"unrated",label:"无评分但销量达标",description:"累计销量达标、无评分，按确认规则允许入池。",source:"selected",sales:460,rating:null,totalPercent:15,publicPercent:10,creatorPercent:13,campaignDays:null,stock:null,offerReady:true,linkState:"verified",identityReady:true,replyOpen:false,cardPercent:13},
 {key:"legacy_only",label:"只有历史旧卡",description:"旧卡不参与发送；按统一命名与分佣规则补建标准链接。",source:"selected",sales:920,rating:4.5,totalPercent:15,publicPercent:10,creatorPercent:13,campaignDays:null,stock:null,offerReady:true,linkState:"legacy_only",identityReady:true,replyOpen:false,cardPercent:12},
 {key:"missing_link",label:"Campaign 缺链接",description:"商品合格但没有当前方案的 TapLink，停在待建链。",source:"campaign",sales:780,rating:4.4,totalPercent:18,publicPercent:12,creatorPercent:16,campaignDays:72,stock:820,offerReady:true,linkState:"missing",identityReady:true,replyOpen:false},
 {key:"product_invalid",label:"Campaign 期限降到 44 天",description:"加入时剩余 72 天，刷新后只剩 44 天；历史线索保留，但退出发送池。",source:"campaign",sales:1600,rating:4.8,totalPercent:18,publicPercent:12,creatorPercent:16,campaignDays:44,stock:900,offerReady:true,linkState:"verified",identityReady:true,replyOpen:false,cardPercent:16,joinedCampaignDays:72},
 {key:"reply_open",label:"达人有未结问题",description:"商品和链接可用，但该达人有未解决回复，全部商品暂停。",source:"selected",sales:2100,rating:4.9,totalPercent:17,publicPercent:11,creatorPercent:15,campaignDays:null,stock:null,offerReady:true,linkState:"verified",identityReady:true,replyOpen:true},
];

function productEligible(s:DemoScenario){
 if(s.source==="selected")return s.sales>=300&&(s.rating===null||s.rating>=4)&&(s.totalPercent-s.publicPercent)>=2;
 return (s.campaignDays??0)>45&&(s.stock??0)>100&&s.creatorPercent>s.publicPercent;
}

export function evaluateScenario(s:DemoScenario):{gates:DemoGateResult[];layer:string;summary:string}{
 const eligible=productEligible(s);
 const gates:DemoGateResult[]=[
  {key:"product",label:"商品资格",state:eligible?"pass":"stop",detail:eligible?"满足当前来源规则":"销量、评分、期限、库存或佣金不符合"},
  {key:"offer",label:"精确 Offer",state:eligible&&s.offerReady?"pass":eligible?"wait":"stop",detail:s.offerReady?"方案字段完整且同源":"等待完整方案"},
  {key:"link",label:"标准 TapLink",state:s.linkState==="verified"?"pass":"wait",detail:s.linkState==="verified"?"当前标准 listId 已核验":s.linkState==="legacy_only"?"历史卡忽略，等待补建标准卡":"等待创建并核验标准卡"},
  {key:"lead",label:"PID 线索",state:eligible&&s.linkState==="verified"?"pass":"wait",detail:eligible&&s.linkState==="verified"?"允许进入首次/到期队列":"材料未就绪，不查询或不刷新"},
  {key:"identity",label:"OECID",state:s.identityReady?"pass":"wait",detail:s.identityReady?"达人身份已解析":"等待 Find 判定"},
  {key:"relation",label:"关系状态",state:s.replyOpen?"wait":"pass",detail:s.replyOpen?"未结回复阻断该达人所有商品":"无拒联、人工接管或未结回复"},
 ];
 let layer="严格发送池 · Ready",summary="这条达人×PID 可以进入 ready；组批时冻结具体 Offer 和 listId。";
 if(!eligible){layer="商品失效 / 不合格",summary="保留历史线索，但退出可发与等待队列；商品恢复后重新计算。";}
 else if(s.linkState==="missing"){layer="待建链",summary="商品合格但没有可用卡；链接核验前不查新线索、不进入发送池。";}
 else if(s.linkState==="legacy_only"){layer="待补标准链接",summary="历史卡不参与发送；按统一命名与分佣规则补建标准卡。";}
 else if(!s.identityReady){layer="待 OECID",summary="线索存在但达人身份未解析，不生成达人×PID 位置。";}
 else if(s.replyOpen){layer="等待回复处理",summary="该达人所有商品位置暂停；问题解决后重新参与排序。";}
 return {gates,layer,summary};
}

export const DEMO_COUNTS={
 collected:10000,qualified:2460,offers:2285,linked:2140,queriedPids:1920,
 leads:12600,handles:3480,resolved:2930,positions:6920,
 pool:{ready:1880,queued:4015,cooling:420,reply:75,invalid:210,waitingLink:320},
};

export function poolReconciles(){return Object.values(DEMO_COUNTS.pool).reduce((a,b)=>a+b,0)===DEMO_COUNTS.positions;}

export const SIMPLE_POOL={
 sendable:DEMO_COUNTS.pool.ready,
 waiting:DEMO_COUNTS.pool.queued+DEMO_COUNTS.pool.cooling+DEMO_COUNTS.pool.reply+DEMO_COUNTS.pool.waitingLink,
 unavailable:DEMO_COUNTS.pool.invalid,
 total:DEMO_COUNTS.positions,
};

export function simpleScenario(s:DemoScenario){
 const evaluated=evaluateScenario(s);
 const product=evaluated.gates.slice(0,3).every(gate=>gate.state==="pass");
 const creator=evaluated.gates.find(gate=>gate.key==="identity")?.state==="pass";
 const send=product&&creator&&evaluated.gates.find(gate=>gate.key==="relation")?.state==="pass";
 return {product,creator,send,layer:evaluated.layer,summary:evaluated.summary,gates:evaluated.gates};
}
