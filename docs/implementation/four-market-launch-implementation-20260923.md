# 四市场正式启动：修复与现场验证

日期：2026-09-23。对应[启动计划](../architecture/four-market-production-launch-plan-20260923.md)。以下把**代码接通**、**本机运行**、**真实平台只读**和**真实写入**分开；首发仍需各市场页面启动与真实回执。

## 已实施

1. BR/MY/UK 共用市场发送 worker 与本地控制。MY 在页面首次点“发送”后才使用一条 canary；卡和文字双确认后发布该账号 `message_send=verified`，后续沿同一持久控制继续。每次建会话、发卡、发文前检查当前开关、窗口、账号维护、关系和标准卡 active/listId/Offer 指纹。全候选扫描不再被预览的前200人截断。
2. 发送意图优先恢复原 `cycle_delivery`；已开始的卡/文字只用原 requestRef、原账号、原正文/卡做平台回查。`/api/send` 的市场 reconcile 在停止或窗口外也只读核验；建会话结果无精确关联时停下，不新建替代会话。收信若在卡后文字前发现新回复，保持/结算原 delivery，不继续文字。
3. 四市场收信：IT 每轮检查最近会话和最旧 checkpoint，并将配置调为12个/30秒；BR/MY/UK 用市场独立的只读 IM worker、游标与 checkpoint。首次发现会话时，如果同一达人已有本项目确认文字发送，以其开始时间为历史边界，之后的回复形成 pending；无已确认发送仍按历史导入。
4. 非 IT 的多轮 Agent 用同市场账号、语言、真实历史和 `service_reply` 执行。页面开关、首条试运行、确认后继续分别由持久状态控制。首条真实回执才发布该账号 `agent_reply=verified`，新判断不得把已发过的旧意图替换掉。IT V2 在真正写入前也重查开关与回复窗口。
5. 失败 schedule 采用一小时退避、最多三次补试，保留先前 run 和持久平台意图。新来源采集先识别同 market/account 的 `collecting` run，并按照原读取模式继续原断点；子进程错误提取稳定脱敏码。长采集期间每30秒仍监督独立 worker；记录真实子 PID 后才放行固定 argv，父进程死亡不释放仍在运行的子任务资源。账号维护可独立回收已退出 worker，维护前排空当前 profile 租约，发布后确认收信是否重新前进。普通 sender 失败有5分钟退避，unknown 不自动重启；维护 worker 异常退出且没有新身份发布时，5分钟后可补试一次。
6. 首页阶段计数只从 `items` 和明确业务字段读取，旧阶段没记录处理数时显示“未记录”；业务失败与收信落后单列。市场发送页把北京时间今日确认、累计确认、滚动24小时新联系预留/卡尝试及近5分钟速度分开；作业页调整了窄屏保存按钮。

## 本机与平台状态

- 迁移 check `ready=true`，没有新 migration。在线备份 `var/backups/state/20260923T074018Z-four-market-launch-20260923` 包含33库、1,320,189,952字节、凭据0，创建时独立校验 `valid=true`。
- 9条9月13–14日遗留 `ready` delivery：逐条核对卡/文字均未开始、无组件回执；1条只建立了会话。使用台账的 `cancel_unsubmitted` 结算，历史和已建会话证据保留；现在旧 `ready=0`、发送 `unknown=0`。
- IT 来源 `it-global-20260921-9bbc623e09e9` 的类目断点原先停在第34页。一次 schedule 补试准确暴露 `scan_already_collecting`、清洗阶段写入0；随后创建一次 IT 完整恢复 run `workflow-2179d4a2faefdf554279343db766`，按原 `--by-category` 继续读取，旧完整 head 保持。后续以该 run 的真实终态为准，不把正在读写成已完成。
- scheduler 已按无 active claim 的安全点更新；BR/UK 收信 worker 已实际连接平台只读并写入对应 checkpoint，MY 只读收信连接成功但平台当前返回0会话。IT 收信已用12个/30秒重启，仍需观察最旧 checkpoint 能否追上；目前四市场真实发送和 Agent 均关闭，新代码的 sender/Agent 尚未做真实平台写入验收。
- 已保存本机启动范围 `outputs/launch-audit-20260923/launch-scope.json`：IT 1,643、BR 1,730、MY 4,388、UK 6,015 位 ready，合计13,776；成员只存本机哈希，不作为新业务台账。真实池会随供给、资格和回复动态变化。
- 建立当前线程的30分钟心跳“**四市场正式运行监测**”，跟踪原断点、收信、资源、unknown、跨日与72小时。正常无变化保持简洁，平台写入结果以真实台账回读为准。

## 验证

- Python 全量 1,118 项通过；新增故障合同覆盖 MY 页面首条状态、逐写停止/窗口、当前卡变更、热点与旧收信公平、残留来源续跑、父子 PID 登记、市场 Agent 原正文及回执。
- Web 164 项通过；TypeScript 与 Next 生产构建通过。四市场 `/api/operations-home`、`/api/inbox`、`/api/agent-replies`、`/api/send` 实际本机回读成功。界面与 API 已显示当前发送/Agent 关闭；MY 缺真实 `message_send` 验收仍显示接入事项。
- 真实平台只读：BR/UK 收信建立1/7个 checkpoint；MY连接成功但当前0会话；IT采集原类目分页持续推进。真实平台业务写入中，IT清洗补试为0；恢复 run 的后续写入和四市场发送/回复以持续台账为准，不能由此文预告成功。

## 尚待真实验收

1. 用户分别在市场发送页明确点击“发送”。MY 先执行一条原账号首发 canary；IT/BR/UK 沿当前已验证能力启动。窗口保持北京时间16:30–24:00；未到窗口时进程等待。首发卡与文字、模板、市场、OECID和回执必须逐项核实。
2. 用户在各市场作业页开启 Agent，再在 Agent 设置页点“开始首条真实回复验证”；首条确认后查看真实内容与回执，再点“继续自动回复”。没有新的合格入站时保持等待，不制造测试消息。窗口为15:00–16:00。
3. IT 原类目采集、完整货盘发布、后续 Campaign/TapLink/Kalodata/OECID/发送池应走到真实终态；收信延迟、平台额度、账号维护、跨日及下一次两天 Campaign 刷新仍需观察。未确认结果只按原意图核验，不盲发。

## 16:30 后真实平台结果与现场修复

- 用户分别在四市场发送页执行首次“发送”。IT、BR、UK 控制各自记录了页面 `runRequested=1`；MY 首次点击因市场注册表仍为 `paused` 被拒绝，零 delivery/零写入。修正 MY 运行注册表为 `ready` 后，用户再次页面点击；首条 MY canary 的卡和马来语文字双确认，项目账号 ACC8 `message_send` 能力由该 delivery 精确证据升为 verified。MY 之后按同一窗口继续正式发送。
- 约16:52 只读快照：本轮新增完整确认 IT19、BR20、MY15、UK18；四市场发送 `unknown=0`。IT 前两位再次触达已真实使用第二条话术 `brief`，另三市场首触达按固定 `standard`；样本中的市场语言、卡片来源和当前 `listId` 均匹配。当前确认数仍在变化，这些不是本轮最终业务总量。
- IT 收信 worker 曾在卡确认后文字确认导致 delivery 从 `running` 变 `confirmed` 时，把可变状态混入不可变 episode 指纹而抛 `reply_event_conflict`；在在线库副本上验证修复后安全更换收信 worker。现有 episode 保留原字节，后续只核对不可变身份与正文，不删改旧事实。BR/UK/MY 收信 worker 同步加载相同修复；当前 checkpoint 分别持续增长，暂无收信错误。
- 发送与收信同账号抢租约时，IT sender 曾因 `live_guard_busy` 退出，BR/MY/UK 曾因 `ProfileBusyError` 进入 attention。现均将账号占用列为短暂等待，并复用用户已有页面授权恢复 worker；未新建发送授权、未因忙换账号。僵尸 PID 误判存活也已在公共进程判断处修复，避免安全退出后无法重新拉起。
- IT 一位达人原有4条我方消息，旧逻辑在发卡前只检查“尚未达到5”，导致商品卡作为第5条确认后文字触及上限。新增“卡+文字需要两个剩余槽位”的发前检查；对已确认的卡保留原证据，只结算未尝试的文字为 `partial_delivery`，不将其算作完整触达，也不补发第6条。单测覆盖第4条前的拒绝和原意图的部分结算。
- IT 原类目 run 在第298页出现 reported total 4,466、唯一 PID 4,465，进入 `partial/category_endpoint_total_mismatch`，未发布。调度器随后启动普通周更 run；当前完整类目 head 仍是 `it-global-cat-20260921-01`。原类目需要在 ACC9 空闲后按重复页补洞/完整复读证据处理，不能自动改成完整；普通周更仍在进行，独立监测不得与修复并发抢 ACC9。
- 最新代码/故障修复后 Python全量1,122项、Web生产构建已通过；真实发送中额外校验卡/文字双回执、四市场账号/语言、unknown、24小时额度及收信。Agent 设置和首条真实回复仍待页面动作及真实入站，不计为已完成AI闭环。

## 17:20 后续运行与普通周更重复页修复

- 用户又明确授权由 Codex 代为开启四市场 AI。已在 IT/BR/MY/UK 页面各自开启 Agent，并分别点击“开始首条真实回复验证”；`agent_reply_setting.enabled=1` 与每个 plan 的 `agent-v2-first-send` 持久事件均已回读。当前已过15:00–16:00，平台 Agent 真实发送仍为0；首条必须等下一回复窗口与合格新入站，确认真实结果后才在对应页面继续自动回复。
- IT 曾在无回复累计第4条之后发卡成功，第5条文字触发上限，形成一张已确认卡、文字未尝试。修复改为**发卡前同时核对卡＋文字两条余量**，并对该旧意图仅结算未开始的文字，保留卡的精确证据。结果页“确认触达达人”现在只算卡＋文字完整确认；已确认但不完整的卡留在商品卡数，未伪装为完整触达。IT sender 已复用原页面授权恢复。
- 同账号收信与发送的短暂抢锁曾让 market worker 退出；现在短退避等待账号空闲，scheduler 同时识别手动页面启动的 `runRequested`。项目 worker/claim 的存活检查排除 macOS 僵尸 PID；因此安全退出不会再以 `kill(pid,0)` 假报存活。约17:20 `/api/inbox` 北京日确认触达：IT89、BR50、MY45、UK48；IT 卡91、文字89，差额单列。四市场发送 `unknown=0`，收信 worker 均运行；IT 老 checkpoint 仍需持续补扫。
- IT 类目 run 首个分区 reported 4,466/唯一4,465，在邻域5页补洞中找到1个真实PID并达到reported total。第二个分区 reported984/唯一983，21页邻域复读新增0且重复页精确复现后，以原 `stableDuplicateRows=1` 合同通过。已有 `scripts/resume-global-category.py` 按同一证据合同顺序完成28个分区；它不替换原 run，也不并发创建另一覆盖 head。进度与停止原因在 `var/it-category-completion.*`。
- IT 普通周更 `it-global-20260923-235726fa0695` 在10,000平台行中有9,998唯一PID，于末页保持 `partial`；现增加 `global_source_query_repair_page` 原请求复读证据与 `--repair-partial-query`。找到缺失PID则按唯一数发布；若重复页全部精确复现且无新增，`--accept-stable-query-duplicates` 才接受单列稳定重复行。该普通周更**尚未**实际修复或发布；ACC9当前用于类目run，待其空闲后再执行读回，之前完整head继续服务。
- 最新 Python全量1,125项、Web164项、TypeScript/Next生产构建、文档检查均通过；新增普通查询缺口补洞/稳定重复行、部分卡不计完整触达等正反合同测试。真实业务仍在继续，测试通过不替代平台回执与72小时观察。

## 18:55 账号等待、吞吐与未决隔离

- BR/MY/UK 的固定 30 秒成功后等待已移除；IT 和其他三市场的只读收信只在认证阶段持有 profile 租约，扫描阶段释放，身份文件与维护状态在每次读取前仍复核。发送继续在整个卡文写入与回查期间独占账号。发送进程短期复用同身份 IM 认证结果，并使用每会话 3 QPS 本地预算；发送池只缓存同一货盘 head 的合格 PID 集合 30 秒，关系、Offer、卡和平台历史逐次复检。MY 自动货盘运营关闭但发送/Agent 已授权时，scheduler 仍自动维持 MY 收信。BR/MY/UK 收信改为20会话/10秒、只读2 QPS；账号暂忙显示 `waiting_account` 并短退避，收信认证进入扫描后主动更新状态，发送端让出空档。
- 用户确认速度目标是**每市场各 30 位/分钟**。上线前 BR/MY/UK 每位固定多等30秒，约每市场1位/分钟；移除后约4–5位/分钟，认证短复用与请求节拍优化后，容量未满时 BR/MY 约7–9位/分钟、UK约4–6位/分钟、IT约5–7位/分钟。均按文字精确确认回查计数。随后做了有界真实并发试验：BR 2通道4 QPS/0.6秒写间隔，4通道5 QPS/0.55秒写间隔，均取得每人卡文双回执、无新增unknown，稳定BR仍约8–12位/分钟；UK 8通道6 QPS/0.5秒写间隔8人用时56.6秒，4通道5 QPS/0.5秒写间隔4人用时29.4秒，均没有达到目标。并发增加了IM/写队列等待，没有作为常驻模式保留；四市场继续使用单账号、逐项回查的正式路径。目标仍未验收，不能用短批配置或领取数声称30位/分钟。
- IT `delivery-767ef8ed335faa90e0c5842ed80ac556` 在建会话请求开始后失去回执，卡与文字均未开始；原 `cycle_conversation_intent` 保持 `inflight`、原 requestRef 未变。只读翻完平台当前748个会话未发现该OEC，但这不足以证明平台创建请求必然失败。按用户本轮明确选择，在单库在线备份 `var/backups/state/second-cycle-before-it-quarantine-20260923T103354Z.sqlite`（integrity ok）后，将该 delivery 记为 `quarantined_unknown`，追加操作证据并把该达人转入人工案件；没有平台写入、没有重建会话或重发。IT 其他达人已复用原页面授权恢复确认发送。scheduler 不再对普通 unknown 每10秒重启 IT worker；隔离项仍在发送页的未决计数。
- BR、MY 滚动24小时新联系已各达500，当前 `waiting_capacity`；建会话和发卡前本地预检额度，原冻结意图保留，worker存活并按额度释放续跑。约18:54只读快照：累计完整确认IT917、BR501、MY500、UK372；最近5分钟确认速度IT5.6、BR0、MY0、UK4.2位/分钟；IT隔离未决1，其他三市场发送unknown0。BR/MY的0是本地额度阻止新增联系，不是吞吐验收。IT/UK继续发送，四市场收信worker在跑；IT最旧checkpoint仍明显落后，后续需持续核对活跃来信时效。收信冷水位选择增加当前关系复核，异常状态记录脱敏目标与阶段用于定位，避免一个无效checkpoint反复拖停轮询。Python 1,134 项、文档检查通过；Web无本轮变更。

## 19:16 再次未知与跨市场收信投影修复

- IT 第二条建会话请求 `delivery-23819a2a7aa3f938433b26e1f182e9b8` 同样无回执、卡文均未开始；独立只读遍历平台当前858个会话，未找到其OEC，身份文件未变化。按用户前次对这一精确场景选择的隔离方式，保留原 `inflight` 请求、零组件提交与未知事实，追加本次858/0证据，隔离这位达人并让其余IT达人的真实发送继续；此举不是认定请求失败。当前IT有2条 `quarantined_unknown` 人工事项，仍计未决。UK一轮 `im_transport_error` 未生成发送unknown，原 worker在退避后安全恢复。
- 定位到 BR/MY/UK 收信偶发 `relationship_missing` 的原因：`Inbox.ingest()` 在加橱窗通知后调用 `observe_showcase()`，其默认 plan 是 IT，导致非IT达人的合作状态投影错查IT关系。现显式传入收信 plan，并为BR跨市场隔离补测试；此前失败轮的checkpoint不伪造成功，重启后按原水位继续。建会话HTTP 200但业务响应不完整/非零时，后续新增脱敏原生码与响应哈希证据，仍保持 `result_unknown`，不自动重试。非IT建会话开始后异常也将 delivery 标为unknown并保存原会话意图，不只在worker状态留一个泛化错误。
- 约19:16只读快照：完整确认IT990、BR501、MY500、UK455；IT滚动24小时新联系498/500，BR/MY各500/500、UK451/500。BR/MY等待额度，IT/UK继续窗口内发送；四市场每市场30位/分钟目标仍未达到，后续需在额度重新释放后重新测量，不把当前平台写入量或并发试验换算成达标。本轮最终Python全量1,139项与文档检查通过；Web业务代码未变。

## 19:22 容量等待与平台读取退避

- IT、BR、MY 新联系预留均达滚动24小时500位，持久sender保持 `waiting_capacity`、原 delivery/页面授权不清空，额度释放后继续复检。UK仍有43位本地额度，出现 `im_http_rejected` 且发送unknown0，普通错误退避5分钟；收信独立持续运行。约19:22累计完整确认IT993、BR501、MY500、UK461。上线代码与页面均不得把本地500限制改成“平台每日限额”或以换号绕开。
- 针对反复的IT建会话无回执，`create_once()` 对HTTP200但业务码非0或响应不完整的情形，只记数值码和响应SHA供下次原意图调查，仍为 `result_unknown`；非IT建会话在请求已开始后异常也把delivery标为unknown并留原requestRef。不能将尚未拿到响应的历史两条意图事后补成明确失败。

## 19:26 UK 原会话回执恢复

- UK `delivery-e947b177740cfd1c40d0c5fd8f55fd6d` 在 `cycle_conversation_intent=received` 已持原请求的 CID/回执，但旧 market sender 把 `received` 与无回执的 `inflight` 一并判为未知并停下。现在仅在原 requestRef、回执CID和同达人平台会话身份同时匹配时，确认原会话意图并继续该 delivery，绝不二次创建会话；失配仍保持未知。该UK原 delivery 已真实完成卡和文字双回执，原会话意图变 `confirmed`，发送unknown0，worker再次持续运行。正反合同测试覆盖原回执及替代requestRef/CID/非候选回执拒绝。
- UK 继续发送时页面曾把 worker 正在处理、文字 `inflight` 的几秒钟也显示为“结果未知1”，但该组件随即取得原回执并完整确认。状态投影现将存活发送进程的正常在途与失主/明确unknown分开；读回 `/api/send?market=uk` 为 `sending/unknown0`，不把瞬时在途伪装为事故。

## 19:33 四市场本地容量终态

- 四市场 `/api/send` 均已回读 `waiting_capacity`、本地滚动24小时新联系 `500/500`；发送worker、四市场收信及 scheduler 仍存活。最早本地额度释放分别是北京时间2026-09-24 IT16:30:07、BR16:30:31、MY16:34:01、UK16:30:11。到时仍须核对发送窗口、原冻结意图、关系/材料和平台额度；不是预告平台一定接受发送。IT有2条隔离未决建会话原意图，其他三个市场当前发送unknown0。30位/分钟**每市场**目标仍未完成，下一窗口按真实文字回查的完整分钟重新测量。

## 21:33 用户纠正 IT 全托范围：普通周更继续

- 用户明确只取消 IT 按类目的完整全托采集，普通全托采集必须继续。原停止的类目 run 与旧完整覆盖基线保持不变。原普通 run `it-global-20260923-235726fa0695` 为10,000平台行/9,998唯一PID；在线备份 `var/backups/state/global-source-before-it-plain-repair-20260923T1327Z.sqlite` integrity ok 后对两个重复页附近按原请求复读。第60页无新增，下一页因平台实时排序漂移会使合并唯一数超过原 reported total，确定性合同拒绝 `query_repair_union_exceeds_total`；原 run 继续 `partial`，没有用不同时间的页面伪造完整。
- IT 的每周全托模式改为只选普通查询，缺已发布类目基线时明确失败；IT CLI 阻止新 `--by-category`，调度器不自动续跑 IT 类目 `collecting` run。`fullCatalogWeeklyEnabled` 在本机备份 `var/backups/state/second-cycle-before-it-plain-weekly-enable-20260923T1330Z.sqlite` integrity ok 后从revision3恢复为revision4/true，自动运营保持开启。到期调度新建仅含 `selected` 来源的 workflow `workflow-9285ce7cb0c7427f9e13c0a67389`，前置清理后实际以无 `--by-category` 的命令启动普通 run `it-global-20260923-0537c0e0577f`。21:33 为63页/945唯一PID，旧完整 head 未替换；发布和后续选入/建链待真实收口。页面文案、API和新Web实例已回读；Python全量1,153、Web164、TypeScript/构建通过。

## 21:13 IT TapLink 完成及 Kalodata 预检故障修复

- 原 Campaign-only 工作流的 `taplink_prepare` 已完成，587个准备项全部 `ready`，阶段记录377次平台写入。随后 `kalodata` 在0.1秒内以 `kalodata-sales_failed` 停住，处理数和阶段写入均为0。根因是调度器调用要求必填 `--market` 的 `leads-run.py` 时对 IT 省略参数；旧 `leads-run.json` 的修改时间早于该阶段，CLI 没有进入首个状态写入或平台读取。同步修正 `lead-pool.py status` 的同类 IT 参数缺口。
- 以原阶段精确状态、零处理/零写入、短时失败、旧状态文件未更新、上游 generation、无 claim 为门禁，新增持久幂等的 Kalodata 预检失败恢复入口。在线备份 `var/backups/state/second-cycle-before-it-kalodata-preflight-recovery-20260923T1313Z.sqlite` integrity ok 后，以请求 `it-kalodata-preflight-recovery-20260923-001` 恢复同一 run 的 Kalodata 阶段；新 scheduler 已用 `--market it` 启动真实只读任务，首个快照111目标、完成2、网络请求5、错误0。此阶段仍在进行，OECID、发送池未宣称完成。Python全量1,151项通过。

## 20:56 IT Campaign 原工作流续跑

- `workflow-038da43ec820264f5b155e183fdd` 的 `catalog` 阶段已发布原 generation，111个活动、9,049条 Offer、平台写入25次且无加入 unknown。`taplink_prepare` 因调度器调用必填 `--market` 的 `catalog-names.py` 时漏参，停为 `needs_human/catalog_short_names_incomplete`；该阶段写入0、后续阶段未启动。修正固定 argv 后，另外按原商品范围补齐73个短名，实时 status 为 scope538/ready538/missing0/invalidSourceTitles0。
- 新增只服务 IT Campaign 这一精确停点的 `resume-short-names`：事务内核对原 run、上游 generation、阶段顺序、零写入、无 claim 和当前短名缺口，持久记录恢复 requestId、旧错误与旧时间；只把原 TapLink 阶段恢复为 queued，重复请求幂等，不重新运行 Campaign。已在备份 `var/backups/state/second-cycle-before-it-short-name-recovery-20260923T1253Z.sqlite`（integrity ok）后执行恢复。新 scheduler PID95251 已领取原阶段，20:55 只读卡核验完成并开始标准卡创建；真实阶段终态及 Kalodata/OECID/发送池仍待回读。全量Python1,148项、文档检查通过。

## 20:36 用户停止 IT 按类目全托采集，转入 Campaign

- 用户两次明确要求停止 IT 完整/按类目全托采集并推进下一步。原 `resume-global-category.py` 父PID87908与 `collect-global-opportunity.py` 子PID87909 属同一新会话进程组，整组收到SIGTERM并在当前页边界退出；两把锁、ACC9 profile均释放。没有删除来源库或重建 run。状态停在原 run 1701页/25,410商品/16个类目完成，旧完整head仍为 `it-global-cat-20260921-01`。在线备份 `var/backups/state/global-source-before-it-operator-stop-20260923T123540Z.sqlite` 校验 integrity ok 后，新领域操作 `stop_unpublished_category()` 原子写 `stopped/operator_stopped_category_collection`、当前分区同态和操作事件；所有已读页与未完成断点留存，不发布半成品。IT 全托自动刷新开关由revision2保存为revision3/关闭，自动运营与发送授权不变；UK全托未修改。
- IT Campaign-only 的下一轮 `workflow-038da43ec820264f5b155e183fdd` 已创建。它最初在 `campaign-join.py status` 失败，原因是 scheduler 使用“IT省略market”的通用 argv，而该CLI要求 `--market it`。修正 status/verify/join-all 三处并添加 IT/非IT argv 合同测试后，安全替换 scheduler并以明确的新 manual run 继续；原失败 schedule run 和平台意图保留。新 run 的 Campaign catalog 阶段已完成：join job completed、无加入 unknown，111个活动、9,049条 Offer，阶段记录平台写入25次。随后 `taplink_prepare` 进入 `catalog-link-batch.py --route campaign --creates 0` 的只读核验，当前仍在运行；Kalodata、OECID、发送池仍须等同一 workflow 的上游阶段结算，不把活动加入或部分商品读取当完整闭环。

## 20:17 IT 类目动态总数断点

- 四市场发送与Agent worker均存活。四个发送页实时回读 `waiting_capacity` 和500/500，当前没有新发送unknown；IT的2条 `quarantined_unknown` 继续单列。Agent设置四市场均启用首条验证，20点已过15:00–16:00回复窗口，真实V2首条仍待下一窗口和合格入站。IT最旧收信checkpoint约96分钟；仅此冷水位不能代替活跃入站SLO，后续继续追踪。
- IT 原类目 run 第13类 `603014` 原末页后记录215页、3225行、3225唯一PID，前145页 reported total 3226、后70页3225，零跨页重复。旧 `partial/category_endpoint_total_mismatch` 是平台动态总数，`partial_repair_scope()` 因没有重复页返回空范围，旧续跑驱动连续空转并以 `round_limit` 停下。驱动现见空修复范围直接报告 `total_drift_without_duplicate_pages`，不再热循环。没有把旧3225直接改成 completed：保留原 run 和旧已发布完整head，在源库在线备份 `var/backups/state/global-source-before-it-partition-retry-20260923T121228Z.sqlite`（114,425,856字节、integrity ok）后，用项目既有 `--retry-partial-category` 只重读这个未发布分区。重读末页后实际为216页、3232唯一PID＝本次reported total，分区 `completed/endpoint_end`；原 run从12/28推进到13/28并进入第14类。此前12个已完成分区和旧head均未替换。`resume-global-category.py` PID 87908与子 worker PID 87909 单实例持锁续读，scheduler PID 88033恢复；若再次出现动态总数且无重复页，驱动会明确停下供证据处置，不重复空转。普通周更10,000行/9,998唯一PID的partial仍须等ACC9空闲后再处理。
