# BDHub-Agent 当前交接

文档更新时间：2026-10-05（Asia/Shanghai）。下面各节按时间倒序，每节只代表该时刻的核验；最新一节即当前生产状态的最近记录，不是实时巡检。规则见 [PROJECT](../PROJECT.md)，实现见 [TECHNICAL](../TECHNICAL.md)，后续方向见[项目审计与清理](../implementation/project-audit-20260924.md)。更早流水见[历史交接](../archive/handoff/codex-takeover-history-20260923.md)。

## 2026-10-05 00:24：经营日报改由 Codex 通报，邮件暂停

- 用户明确要求先不发邮件，改为通过 Codex 接收。已将 `private-data/business-daily-mail.json` 的 `enabled` 设为 false；卸载 `io.bdhub.agent.business-daily` 并移除其用户 LaunchAgents 入口，保留历史代码和报告。Gmail 发件凭据未取得，本机无日报投递记录；不继续 Google 登录或应用密码创建。
- 已创建当前对话的每日心跳“BDHub每日经营日报”（`bdhub`，ACTIVE），北京时间每日 09:00。复用 `scripts/business-daily.py preview` 只读真实台账，在当前 Codex 对话完整展示分市场表格与末尾汇总；排除 UK/IT，保留累计口径，不展示 GMV／发布／财务，也不调用邮件 tick。未变更“完成泰国自动运营开启”等其他心跳。
- 手工验证 10/04 报告生成成功，五市场无数据读取缺口；库存实际采样 10/05 00:16。可用商品 16,304，TapLink 就绪 13,299／81.6%；新增 A/B 2,967／409，完整发送 3,156 位，剩余线索 11,744；累计回复率 6.3%，累计橱窗通知／确认卡比例 5.6%。当前未回 288 位，其中等待超过 24 小时 122 位。
- 原 `config/jobs.json` 运营改动保留；未修改业务库、运营控制，未启动或重启 TikTok worker，平台写入 0。通报需要本机项目可访问且 Codex 应用运行，详见[每日经营日报](../implementation/business-daily-mail.md)。最新人工验证导出在 `outputs/business-daily-codex/latest/`。

## 2026-10-04 09:35：分市场经营日报已生成，定时任务等待发件配置

- 用户确认表格日报：除英国、意大利之外的所有运营市场分别展示商品／TapLink、当日新增 A/B 线索、发送与剩余池、累计回复／橱窗及待办，最后汇总；取消 GMV、发布、财务和 7 日／批次分析。收件人由用户提供，保存在 `private-data/business-daily-mail.json`；发送时间暂按建议北京时间 09:00。
- `0f19d10` 在独立 worktree 开发验证后合入生产。相关 Python 64 项和文档检查通过；真实五市场报告及桌面／390px 显示核验通过。6 张既有控制表摘要与 `config/jobs.json` 原运营改动 SHA256 未变；未重启业务 worker、Web 或 scheduler，TikTok 写入 0。
- 首份报告事件日期 10/03，库存实际采样为 10/04 09:34:52，不伪称历史日末库存：可用商品 16,304（全托 5,380、Campaign 10,924），TapLink 就绪 13,299／81.6%；当日新增 A/B 16,836／171，完整发送 3,707 位，累计橱窗通知／确认卡观察比例 5.6%。现有橱窗通知没有 PID/listId，因此邮件明确标为通知口径，不宣称逐卡采用转化。
- 独立 LaunchAgent `io.bdhub.agent.business-daily` 已安装并首次运行 exit 0；按北京日保存报告，09:00 后发件，已成功／提交中／未知均不重复提交。当前 `waiting_mail_configuration`，实际发件 0：尚未收到用户对发件邮箱／服务及认证方式的答复。后续补齐私有 SMTP 配置后再验证服务商接受回执，不能称邮件已经送达。
- 口径、运行与恢复见[每日经营邮件](../implementation/business-daily-mail.md)。本机证据 `var/releases/business-daily-20261004/release.json`；冻结报告 `var/business-daily/reports/2026-10-03/`；用户可读版本 `outputs/business-daily-real-20261004/`。

## 2026-10-04 08:05：用户委托代选泰国方案，AI 全量放行，安排主链自动完成

- 用户在原“全流程打开”要求上明确委托 Agent 代选方案。已选择保留大于 45 天的期限规则，等待合格活动开始后完成加入验证；本次实时刷新仍无合格未加入活动，Raiyusiri `7692131618631943956` 尚未开始、yuna 剩余期限不足。
- TH 首轮 AI 回复已确认，ACC14 `agent_reply` 和 `message_send` 均 verified。经正式 `/api/agent-replies?market=th` 的 `resume-full` 保存全量授权，读回 `rolloutStage=full`、enabled=true；沿用 00:30–08:00 窗口。发送持久控制仍为 automaticEnabled/runRequested=true、stopRequested=false，当前窗口外等待，unknown=0。
- 已创建当前 chat 的心跳“完成泰国自动运营开启”（`automation-2`，ACTIVE），首次计划执行北京时间 10-05 01:05，之后每日同一时刻重查直至完成。流程为实时活动资格核对→ACC15 唯一 Campaign 加入 canary/原申请核验→真实回读确认后能力晋升→按现有接入与发布流程启用协议层能力→正式首页开启主链及持续发送，TH 全托发现保持关闭；尊重用户后续暂停，不重发未知申请。未变化时静默，完成或新故障时通知，完成后暂停心跳。
- 本轮只有运营授权与交接变更，未重启业务进程，原 `config/jobs.json` 变更保留。回执、刷新结果与后续安排证据在 `outputs/th-full-activation-20261004/`。

## 2026-10-03 22:28：用户要求开启泰国全流程，持续发送已开始，主链仍待加入验证

- 用户明确要求“把泰国也发起来，全流程打开”。通过 TH 发送页执行“开始发送”并保存持续自动发送：`runRequested=true`、`automaticEnabled=true`、`stopRequested=false`，修订 2→3→4，沿用北京时间 16:30–24:00、ACC14 和已审批泰语模板。首次启动 IM 读取超时，原 scheduler 按已有间隔重新拉起后正常发送；核验截图今日确认触达 27 位（此前 1 位），unknown=0。
- 自动运营主链尚未开启：实时读取仍只缺 `campaign_join`，没有伪造能力验证。刷新后已加入 707 场，两个未加入候选均不符合现行加入条件：`7692383262342760210`（yuna x affiliate）11-01 00:59:59 到期，不足 45 天；`7692131618631943956`（Raiyusiri）10-05 01:00 才开始，2027-01-01 00:59:59 结束。主链首页开关仍关闭；发送页的独立持续控制已生效。
- AI 回复原设置保持开启，窗口 00:30–08:00，当前 `pilot_running`，尚无首轮确认回执，未写入全量放行记录。TH 不支持全托，未启用全托发现。另一会话的身份导入未停止。
- 已询问用户：保留 45 天规则并在 10-05 自动完成，或仅为一次加入验证允许短期限 yuna；本快照尚未得到答复，因此没有加入短期活动或创建未来自动任务。证据在 `outputs/th-full-activation-20261003/`，含开启前状态、原页面请求回执、实时 Campaign 日期和发送截图。

## 2026-10-03 21:20：前端信息精简上线

- `da9b46e`：精简共用导航和重复说明；总览并列市场表现与人工待办，窄屏前置待办并改为市场卡片；运营首页前置库存、控制和运行事项；发送状态与操作集中；货盘前置商品入口。历史、统计口径和健康诊断默认折叠，未知、异常与旧数据提示保留。
- Web 208 项测试、build/typecheck 和文档检查通过；四个重点页面完成桌面与窄屏检查。新构建 `QE2f4OwsHKtmfULqm5t2J` 的 291 个文件校验一致后，于 21:12:58 只切换 Web；生产四页面及六个只读接口返回 200，浏览器无控制台错误。截图、审计笔记和验收记录在 `outputs/ui-simplification-20261003/`，旧构建保留于 `var/web-releases/ui-simplification-20261003/previous`。
- 七张授权/控制表摘要及原 `config/jobs.json` 运营变更保持不变；本任务没有调用平台写入或启动/停止业务任务。其余 18 个常驻进程 PID 保持；BR sender 在 21:12:19 因 TikTok IM TLS `SSLEOFError` 自行退出，早于 Web 切换约 39 秒，新总览正确显示“发送已停止，需关注”。21:17:29 原 scheduler 按既有任务重新拉起 BR sender，核对父进程及授权未变，现为等待候选；本任务未手动恢复。

## 2026-10-03 20:22：全面审计修复上线，46 库恢复演练通过

- 审计 A1–A8 已修复：首条二发/AI 授权与能力恢复、paid 最终门禁、Campaign 原证据结算、TapLink 原市场/账号核验、总览实时通道、备份及文档一致性。细节、边界与提交见[本次交付](../implementation/audit-fixes-20261003.md)。无业务库结构迁移，未新开任何发送或 AI 全量授权。
- 最终 Python 1,721 项、Web 208 项通过，build/typecheck/文档检查通过。19:57 已切换 Web 与常驻代码；postflight 全通过：七张控制表摘要不变，原 60,662 条 requestRef 映射保留且全表唯一，19 个常驻角色一致，七市场 IM owner ready。原 `config/jobs.json` 未提交运营变更完整保留。
- 备份清单含 46 库。两次先父库后子库的副本被演练正确拦下：另一 Claude 会话的 TH 身份导入循环继续写入，造成备份间引用时间差，现场库无缺口。保持该任务，改为先引用库后追加式父库；`20261003T121047Z-audit-fixes-ordered` 校验及正式隔离恢复演练通过，blockers 为空，身份依赖提示保留、不重发 unknown。
- 经用户明确批准，校验并确认未占用后只删除 09-29 的 `20260929T132106Z-pre-audit-b798911`（3,210,859,560 字节）；10-01、10-02 与其他备份保留。新备份位于 `/Users/bjn00003/BDHub-Agent-backups/state/`。
- 现场旧 scheduler/MY sender 在 19:34 左右因库锁退出；核对无停止请求、控制不变后恢复。补齐首次打开库/裸 SQLite busy 的等待及失败连接关闭，不把其他数据库错误吞掉。生产回读未再出现调度器停止、版本不一致、备份阻断或恢复失败告警。
- 本机证据：`var/releases/audit-fixes-20261003/`；当前发送保持原设置（IT/UK/TH 关闭，其余按原窗口/额度/供给继续），历史人工事项和未回队列未批量结算。代码已合入生产分支，未推送远端。

## 2026-10-03 17:55：常驻进程重启到当前代码，修复发送进程遇库锁停发

- 按 `ops-restart-check.py` 流程完成重启（最终 `restart-20261003-1750`，postflight 全部通过，版本不一致告警消除）。
- 发现并修复：非 IT 发送许可写账遇 SQLite 锁时被适配器包装成 `it_delivery_dispatch_not_allowed`，worker 进入 attention 并冷却 5 分钟。10-03 BR/MY 17:21–17:31 停发（期间调度器正在排空，冷却后无人拉起），DE/FR 17:04–17:10、DE/MY 17:42–17:47 各停一次；均无在途或未知发送。`977a849` 让 attention 退出写日志，`aafd3c9` 把未提交组件的库锁改为 `local_store_busy` 短等待重试。
- TH OECID 滚动批次在重启时被库锁打断，已重新开跑。

## 2026-10-03 17:25：法国开关开启，泰国首发完成、自动运营待 Campaign 加入验收

- FR：用户审批法语模板后页面启动首发（16:30 confirmed，`message_send` verified），随后打开自动运营、全托周更、持续发送，AI 回复按 DE 配置启用并授权首轮 pilot。
- TH：`b312ead`/`185afea`/`e471503`/`c691d08` 接入 ACC14/ACC15（region 5、SG host、无全托货盘）；Campaign 货盘读取遇平台瞬时码会重读（最多 3 次）。TapLink 2,016 条 ready；泰语模板用户已审批；AI 回复已启用并授权 pilot。带画像的身份 canary 画像阶段连续 `100000`，改用 identityOnly canary（find code 0）验收 ACC15 `oecid_find`，身份滚动批次已开跑。17:20 用户页面启动首发，ACC14 卡＋文字 confirmed，`message_send` verified，worker 按 `canary_confirmed` 自停。Kalodata A 类当日额度用尽，次日续查。
- TH 自动运营与持续发送被 `market_automation_capabilities_pending` 挡住，只缺 `campaign_join`：唯一候选活动 Raiyusiri `7692131618631943956` 北京时间 2026-10-05 01:00 开始，届时 canary 加入→promote→协议层 TH campaign/campaign_join 置 enabled（重生成 vendor patch）→打开自动运营与持续发送（全托周更不适用）。
- 常驻进程仍运行命名修复前代码（版本不一致提示），待调度器空闲按 `ops-restart-check.py` 流程重启。

## 2026-10-03 13:45：德国开关开启，法国货盘铺开完成

- 用户纠正：Kalodata 额度按市场独立（已存记忆）。用户授权代办「法国登录、模板审批、德国开关」：法语模板审批被权限系统判为自我审批而拒绝，仍需用户在页面审批；其余已办。
- DE：10:12 经运营设置打开自动运营、全托周更、持续发送（send control 联动 automaticEnabled），AI 回复按 BR 配置启用（回复窗口 00:30–08:00）并授权首轮 pilot（`agent-v2-first-send`）。首轮完整流程中 taplink_prepare 因 5 个短名 `provider_output_invalid` 被锁转 needs_human：`0de1d92`/`b1ce1a1` 把「已返回但不可用」的模型答复（output invalid/truncated）按 names_invalid 重问、prepare 不再跳过；`8a0a705` 非 IT 短名缺口只看每条路由最新准备批次（旧批次的 read_incomplete 曾永久挡住恢复）；`resume_short_names` 放开到所有市场与两种来源。补名后恢复，主流程 6 阶段完成，DE 可发送达人 782、位置 2,873；身份积压按滚动轮次继续。Kalodata A 类约 2,090 PID 后当日额度用尽，余 1,931 由流程续查。因调度器持续运行 DE OECID，今天合入的命名修复尚未重启常驻进程（巡检会提示版本不一致，待空闲按流程重启）。
- FR：`0e71a52` 协议层固定市场名单补 fr（首次登录验证曾 `commerce_market_invalid`），ACC16/ACC17 首登均无需验证码、同机构；`c6e993e` FR ready（开关全关）、全托、Campaign/加入 enabled。canary：Campaign 加入 ORICO.FR、TapLink 1 条、选入 1 个、身份查询（找到并读到画像，`oecid_find`/`creator_profile` verified）、ACC16 IM 只读初始化（`inbox_read` verified）。全托 28 类 44,382 商品（男装稳定重复 1 行），筛出 2,082、选入 2,079 confirmed/3 skipped_unknown；Campaign 加入 139（共 140，平台已加入列表暂显示 103）、筛出 551；建链 Campaign 551、全托 2,079 全部 ready。Kalodata 查约 290 PID 后当日额度用尽。备份清单补 DE/FR 全托与 FR 线索库（43 库，inventory ready）。
- 待用户：FR 16 条法语模板审批；FR 首发需页面启动；FR 是否开启自动运营/持续发送/AI 回复。

## 2026-10-03 04:20：德国货盘铺开完成，法国接入到首次登录前

- DE 全托：新批次类目读取完成并发布（28 类、46,013 商品；女装 `endpoint_end_stable_duplicate_rows_1`、运动与户外 `endpoint_end_total_drift`），筛出 3,309；用户确认后选入 canary（PID `1729480004826732625`，写 1、经平台验证后 confirmed，`product_select` verified），批量选入 3,304 confirmed、5 `skipped_unknown`；已选同步 3,304 offer，德语短名 3,304（模型 661 次），建 TapLink 写 3,304、全部 ready。
- DE Campaign：718 ready（另 5 个短名模型调用 unknown 被锁、1 个读取不完整）。DE 可发送达人 182（仅首批 91 PID 跑过 Kalodata/OECID）；其余线索与 438 个待身份 handle 留给开启自动运营后按日常节奏处理，未一次性消耗 Kalodata 共享额度。DE 自动运营/持续发送/AI 回复开关仍全关，收信不运行。
- FR：合作伙伴信息只读查得地区 17（14 为西班牙，以商品标题语言区分），`e6bc2b4` 协议层加入 FR（EU 形态，Campaign/加入 pending）、欧洲 IM 地区加 17、注册表 `paused`、ACC16 通讯/ACC17 供给、fr-FR 内容（审阅稿 `implementation/fr-content-review-20261003.md`）。04:10 按流程上线（postflight 通过），`onboard-markets` 建 FR plan。FR 数据库尚未生成，生成后需把 `campaign-join-fr`、`campaign-screen-fr`、`kalodata-leads-fr` 加入 `config/state-backup.json`，否则备份清单不就绪。
- 待用户：FR ACC16/ACC17 首次重登（邮箱验证码）；FR 16 条法语模板审批；DE 是否开启 AI 回复/持续发送/自动运营。

## 2026-10-02 23:00：德国首发完成，Campaign 货盘铺开，全托读取进行中

- 线索/身份：用户要求跳过 DE 的 Kalodata 1 小时等待（只改 DE 行，等同成功后的代码更新）。用户确认后给 91 个候选 PID 建 TapLink（写 90）；A 类查询 91 PID、150 次请求；身份队列跑出 182 位可发送达人（另有 438 个 handle 待日常处理）。ACC13 `oecid_find` 经只读 find 探测（`var/releases/de-identity-canary-20261002/`）记为 verified。
- 首发：`c651415` DE 转 `ready`（开关全关），非 IT canary/发送控制按注册表，未验证 `message_send` 的市场只发一条页面首发并自动 stop；`08a8525` 卡片发送者改为市场配置的通讯账号（原写死 IT/BR/MY/UK，DE 首次启动在写入前被拒为 `it_delivery_auth_invalid`）。ACC12 `inbox_read` 经 `probe-market-im.py` 只读初始化记为 verified（`var/de-im-read-canary-20261002.json`）。用户页面启动后 21:59 发给 @mopamintestet（JONR 吸尘器，卡＋文均 confirmed，unknown 0），worker 随即 `canary_confirmed` 停止，`message_send` verified。DE 发送已停、AI 回复未开，DE IM 会话因此不运行（收不到回复，待用户决定）。
- Campaign：`efefff0` 协议层 DE `campaign`/`campaign_join` 改 enabled（批量加入原被能力门拦在写入前）。用户选「按规则全部加入」：184 次加入全部受理（共 185），重采 10,920 offer、筛出 724 PID；命名 719（5 个模型调用 unknown 被锁，系统不自动重试，未处理）；建链写 636，718 ready、1 读取不完整待重试。
- 全托：首轮类目读取在第 1 类第 140 页遇平台瞬时 16201010（随后只读探测 ACC13 正常），旧批次 `de-global-20261002-18b1668905dd` 停在 blocked（续跑需 relogin 代次）；新批次 `de-global-20261002-98fa0963079d` 22:59 起后台读取。读完后：筛选 → 选入 1 个 canary → 用户确认数量后批量选入与建链。
- 期间 BR 22:10 一条正文回执无法解析记 unknown，22:15 按原 requestRef 回查 confirmed（与本轮改动无关）。

## 2026-10-02 21:20：德国写入 canary、协议子进程与模板审批修复

- 德国：ACC13 只读实测全托可用（`var/de-full-managed-probe-20261002.json`，`b11ff10` 标记 `fullManagedCatalog=true`）；用户确认后 Campaign 加入 canary（Deedlite 居家挂钩 `7646320501174585110`→平台活动 `7692026737261774614`，写 1、读回 joined）与 TapLink canary（PID `1729797683098721260`，list `8650985608433802006`「🔥 BJN Edelstahl Saugnapfhaken Set 11% 5e6cd5」，写 1、读回一致），两项经 `promote-canary` 记为 verified。Campaign 采集 1,127 offer，筛出 91 PID 均已生成德语短名。
- `a495931`：协议层配置根迁到 `private-data/` 后，平台验证求解等 `python -m bdhub...` 子进程以 `private-data/.venv/bin/python` 启动，既无解释器也无 vendor 包，任何读取/选入遇到验证都会失败（切换后尚未在生产触发）；改为导出 `BDHUB_CONFIG_ROOT`/vendor 路径并用 `sys.executable`，DE 实测验证求解成功。vendor `local.patch`/manifest 同步（含 DE 能力改动），`--check` 恢复通过。
- `7e5f13e`：平台活动列表总数字段改为 `total_num`，所有市场 Campaign 预览会报 `campaign_list_incomplete`（BR 上次加入为 09-25，未在生产触发）。
- `df05f8d`：发送模板审批改为按市场（schema v27），迁移前在线备份 `20261002T131220Z-pre-template-review-20261002`（36 库，valid；09-28 备份移入废纸篓），`1fd32fa` 备份清单纳入 DE 三库。迁移后 IT/BR/MY/UK 各 16/16 approved，DE 0/16 待用户审。postflight 全部通过，BR/MY 发送 `waiting_pool`。
- Kalodata：cookie 在 14:51 后失效，五市场线索查询 401（`kalodata_auth_required`，各市场等待 1 小时重试），用户 21:04 刷新后 probe ready；BR/MY/DE 分别在 21:52/21:50/22:00 自动重试。巡检尚无 Kalodata 登录失效告警。

## 2026-10-02 19:55：德国登录验证与德语内容上线；BR/MY 发送中断约 5 分钟

- acc12（19:20）、acc13（19:32）首次重登完成并发布 relogin 代次，机构指纹一致；通讯侧机构/IM 身份/商品卡、供给侧货盘读取/Campaign 已 verified，写入能力未测。
- `8b5ed6c`：DE 德语内容（用户确认审阅稿 `implementation/de-content-review-20261002.md`）与 `contentReady=true`，DE 仍 `paused`；线索队列、Kalodata 视频扫描、IM 会话、收信历史等市场范围改按注册表。19:44 按流程上线。
- 事故：发送模板审批指纹把所有市场的同 ID 正文一起计算，加入德语正文使 16 条已批模板全部变为 pending，BR/MY 发送 worker 启动即 `send_template_approval_required` 退出（未发送任何消息）。`135f818` 指纹只计 `ready` 市场，德国转 ready 时其正文再进入审批；改后对生产库只读复核四市场 16/10 approved。19:49 重启后 BR/MY 发送恢复 `waiting_pool`，postflight 角色与中断前一致（基线因 worker 已退出而缺两项）。

## 2026-10-02 18:10：德国分配 ACC12/ACC13，待首次登录

- 用户经账号页登记 acc12–acc17（DE acc12/13、TH acc14/15、FR acc16/17，partner_id 同机构），密码由用户输入。`5c1a076`：DE 改为 `paused` 并分配 ACC12（通讯）/ACC13（供给）；`paused` 允许内容与能力未验收，发送等业务入口仍只认 `ready`。
- 上线：preflight（在途 0、unknown 0、requestRef 52,578 唯一）→ scheduler 空闲退出 drained 通过 → 10 个 worker 退出 `--workers-stopped` 通过 → 快进合入 → Web 构建逐文件校验 291 个文件后切换（先关闭内置浏览器标签，避免旧进程挂起）→ scheduler `13944` → postflight 全部通过。`onboard-markets.py apply --confirmed` 建 DE plan；DE 账号接口返回 acc12/acc13、无身份代次，巡检无 DE 读取错误。
- 18:28 acc12 首次重登：登录成功但每次候选验证都被协议层 DE `product_search=pending` 拒绝，异常被吞、可见浏览器等满 10 分钟后 `account_login_timeout`。只读复验（跳过该门）：机构、IM 身份、商品卡、Campaign 读取均通过。`899f72f` 启用 DE `product_search`、`campaign` 改 pending；验证失败改记 `account_validation_failed:<原因>`，能力门错误立即结束。同流程重启（scheduler `20538`，postflight 全部通过）。
- 下一步：用户在 `/de/ops/accounts` 为 acc12、acc13 发起首次重登（可见浏览器，邮箱验证码由用户输入），验证机构与能力；随后补德语内容、Kalodata 区域与逐能力 canary。TH/FR 尚无协议层市场定义。

## 2026-10-02 17:50：IT/UK 改为单账号，释放 acc9、acc4

- 用户要求 IT、UK 只做收信与 AI 回复、各占一个账号。`34ab5b6`：市场可只列一个账号、两个角色指向同一账号（IT acc6、UK acc11），单账号市场按通讯角色只登记一次维护；OECID 路由按 `identityAccountRole` 取角色。`68e6e61`：账号状态接口接受单账号市场。acc9、acc4 仍在账号登记表中、未分配，不再定期维护，留作新市场备用。
- 上线：preflight（在途 0、unknown 0、requestRef 52,368 唯一）→ scheduler 空闲退出 drained 通过 → 10 个 worker 退出 `--workers-stopped` 通过 → 快进合入 → Web 构建 `4ZAOFKZByR7t37OY18wFp` 逐文件校验 291 个文件后切换 → scheduler `7222` → postflight 全部通过（四会话 ready）。随后发现账号状态接口仍要求两个账号（503），修复后只切换 Web 构建 `vjbGu6CrvWsRcf5NCsyq2`（旧构建在 `var/web-releases/single-account-*/previous`）；切换时旧 next-server 因内置浏览器标签保持连接未退出，页面约 6 分钟不可用，关闭标签后恢复。四市场账号接口与账号登记接口回读正确。
- 进行中：新增 DE/TH/FR（用户确认完整链路，每市场两个账号）。6 个新账号计划为 acc12–acc17，密码须由用户在账号页“账号登记”中输入；TH/FR 尚无协议层市场定义。

## 2026-10-02 15:40：本项目移出原始项目所在目录

- 用户确认两个项目分文件夹存放。15:38 经带检查的脚本迁移：preflight → scheduler 空闲退出 drained → 10 个 worker 退出 `--workers-stopped` → 移除 Web 任务 → 确认无进程引用旧路径 → `BDHub/BDHub-Agent` → `/Users/bjn00003/BDHub-Agent`，`BDHub-Agent-backups`、`BDHub-Agent-sdk-probe-20260926` 同级迁出 → `git worktree repair`（含 `.claude/worktrees/dreamy-hamilton-df1cba`）→ `.venv` 启动脚本与 `pyvenv.cfg` 路径改写 → `launchctl submit` 在新路径重挂 `io.bdhub.agent.web` → scheduler `86551` → postflight 全部通过 → 页面与接口 200。停机约 1 分 30 秒；无库结构变更，未另做备份。
- `/Users/bjn00003/BDHub/` 现在只放原始项目及其备份/归档（`01-BDSystem-V2`、`backups`、`deploy-backups`、`_archive`、`outputs`），原始项目未动。工作目录仍在旧路径的历史 Claude 会话无法继续使用，需在新路径重新打开。

## 2026-10-02 15:25：新项目独立运行，账号由本项目自己登记

- 用户确认：原始项目（`01-BDSystem-V2`）与本项目作为两个独立项目并存，原始项目保持不变、账号自管；同一 TikTok 账号只归一个项目，本项目 acc1/2/4/5/6/8/9/11，原始项目 acc3/7/10（其账号维护自 09-21 起未再碰本项目 8 个账号，今天 14:27 仍在维护 acc7）。
- `44827e7`：协议层配置与数据根目录改为本项目 `private-data/`（git 忽略，解析到项目外即拒绝）；20 余处原始项目路径全部移除（账号与密码、`config.yaml`、签名运行环境、Kalodata 浏览器锁、DeepSeek 密钥备用来源及协议层写出的运行状态），测试扫描 scripts 与 Web 源码禁止再引用；U 盘备份带上 `private-data/`，换机不再需要原始项目。`973f4b3`：账号页新增“账号登记”（新增账号、改用户名/密码；密码经 stdin、只写不回显、0600 原子替换），登记不等于分配。Python 全量 1,618 项、Web 203 项通过，build 与 typecheck 通过。
- `private-data/` 一次性从原始项目只读复制生成：本项目 8 个账号的登录记录（登记市场改为各自分配市场）、去掉 `db`/`public_share_link`/`sample_review` 的配置（加载器强制要求 `db` 段，填一个无人监听的占位地址，误用数据库会直接失败）、签名运行环境与 `pydeps`（哈希校验通过），共 180M；原始项目文件未改动。今后两边各自改密码。
- 上线：preflight（在途 0、unknown 0、requestRef 52,234 唯一）→ scheduler 空闲退出 drained 通过 → 10 个 worker 退出 `--workers-stopped` 通过 → 快进合入 → Web 构建 `kK27hXdokU9xZSp7fuKi4` 逐文件校验 291 个文件后切换（旧构建 `0Bv92chKmTQx2swXcBYB8` 在 `var/web-releases/owned-runtime-*/previous`）→ scheduler `83774` → postflight 全部通过（四会话经新根目录认证 ready）。页面与接口 200，账号登记接口返回 8 个账号、不含密码；本项目进程无一打开原始项目文件。未强制重登，8 个账号首次用本项目密码登录将在定期维护（10-03 acc1/acc8/acc9，10-04 其余）发生。
- 待办：原始项目里这 8 个账号的记录由用户在原始项目中自行停用；`vendor-legacy-bdhub.py`、`audit-tiktok-*` 是只读原始源码的离线工具，仍保留。

## 2026-10-02 14:50：UK/IT 只保留收信与 AI 回复，IT 发送与回复脱离旧库

- 接口巡检（只读，零平台请求）：机构页面改版后，四市场会话 ready、收信连续错误 0（达人回复入库至 13:47），UK/MY 07:00–07:58 类目读取（59,208/16,803）与 TapLink 建链（平台写 4/34）完成，未见响应结构或鉴权异常；OECID 自 09-28 起无待查对象、未实际调用，发送接口当日窗口前未调用。
- 10-01 15:27 系统更新关机时旧项目 Docker 容器 `bdhub-pg`（PostgreSQL）以退出码 0 停止，开机后 Docker 未拉起（重启策略 `unless-stopped`；推测干净停止被视为手动停止，未证实）。IT 发送与 AI 回复写入前只读检查旧库，读不到即 `legacy_state_unavailable`：10-01 16:30–24:00 IT 发送窗口零发送（上次成功 09-30 21:00），10-02 IT AI 回复 149 轮全部失败；告警未覆盖。用户 14:26 手动启动 Docker 后检查恢复（旧库无 IT 在途）。同一关机使 IT OECID 子进程无输出（`oecid_report_invalid`），身份通道按规则挂起至 acc9 下次重登（10-03 14:40）；现已随 IT 主链停用。
- 用户要求 UK、IT 只保留收信与 AI 回复：14:39 经首页保存接口关闭两市场自动运营、全托商品发现、持续发送（IT 修订 7→8，UK 5→6），并经发送接口正式停止（`runRequested` 清除、`stopRequested` 置位；IT 控制修订 31、UK 17）。IT 收信原挂在自动运营开关下，为保持收信先打开 `config/jobs.json` 的 `inbox_monitor`（运营设置，未提交）。IT/UK 发送进程随即退出；账号定期重登因 BR/MY 自动运营仍开而照常。恢复需在首页重开开关并在发送页明确启动。
- `5fa1ca1`：IT 发送与回复不再读取旧项目 PostgreSQL 与旧发送门禁文件（旧系统最后一次 IT 发信 09-08 12:42，旧库 IT 在途全部为空），节奏仍由本项目 `var/im-http-write-gates/` FIFO 控制；之后若从旧系统给 IT 发信，两边互不知情。Python 全量 1,613 项通过（移除 5 项旧库检查用例）。上线：preflight（在途 0、unknown 0、requestRef 52,234 唯一）→ scheduler 空闲退出 drained 通过 → 10 个 worker 退出 `--workers-stopped` 通过 → 快进合入 → scheduler `78413`（14:47）→ postflight 全部通过（控制摘要不变、角色一致、四会话 ready）。无库结构变更，未另做备份。

## 2026-10-01 17:25：系统自动更新重启、IT/UK 收信中断修复上线

- 15:31 macOS 自动安装 27.0.1 并重启（scheduler 当时在 OECID 阶段），项目进程全停。15:48 preflight（在途 0、unknown 0）后重挂 Web 与 scheduler。UK 会话状态文件记的 PID 3205 被重启后的 Chrome 复用，`process_liveness.pid_alive` 只看 PID 存在，scheduler 一直不拉起 UK；确认该 PID 非本项目后清除记录才恢复（17:32 已由 `c7461c7` 修复，见本节末条）。
- IT/UK 收信中断原因：通讯账号 acc6/acc11 的平台登录在真实重登后约 72 小时失效（9-28 14:31/14:34 → 10-01 14:31 起），欧洲站不跳转登录页、SDK 停在 0、HTTP 照常可用，worker 只报 `sdk_not_ready` 不触发恢复；定期维护又按 +72 小时向后取次日时段（10-02 14:30）。同样情况 9-27/28 已发生过（IT 断约 21 小时、UK 约 14 小时）。`refresh` 只重读现有登录不续期（acc8、acc1 refresh 后仍按上次重登过期）。16:08 手动登记 refresh，二者转重登，16:13/16:16 会话就绪。
- `0305044`：维护基线只认真实登录；定期维护取失效前至少 30 分钟的最后一个角色时段并登记 `relogin`（稳态约 48 小时一次）；会话页面 partner 接口返回 16201010 时报 `sdk_login_required` 进入统一恢复链（实测失效 profile 返回 16201010，正常账号约 105 次调用全为 0）。Python 全量 1,614 项通过。
- 上线：在线备份 `20261001T092003Z-pre-login-expiry-20261001`（33 库，valid），`20260928T124246Z-pre-auth-refresh-20260928` 移入废纸篓。preflight → SIGTERM scheduler（空闲，17:21 退出）drained 通过 → SIGTERM 11 个 worker，`--workers-stopped` 通过 → 快进合入 → scheduler `22168` → postflight 全部通过（含角色一致、四会话 ready）。新规则随即补登已过或临近失效的供给账号 acc2/acc5/acc4（17:22 重登完成）；下次维护：通讯 10-03（acc1、acc8）/10-04（acc6、acc11）14:30，供给 10-03（acc9）/10-04（acc2、acc5、acc4）14:40。自动安装 macOS 更新仍开启，需用户在系统设置关闭。
- `c7461c7`：记录里的 pid 若被启动时间晚于记录写入时间的进程占用即不算存活（`process_liveness.record_alive`；状态文件取 `checkedAt`/`startedAt`，阶段租约取 `heartbeat_at`，维护意图取 `started_at`），覆盖 scheduler、会话、发送、AI、收信、作业、运行登记全部调用方。上线前对 33 条生产记录比对新旧判断无差异；Python 全量 1,618 项通过。17:31 同流程重启（preflight → scheduler 空闲退出 drained → 11 个 worker 退出 `--workers-stopped` → 快进合入 → scheduler `25287` → postflight 全部通过），一分钟后各角色均单进程、无运行告警。未另做备份（无库结构变更，17:20 备份为最近恢复点）。

## 2026-10-01 11:00：磁盘写满停机、开机恢复与空间清理

- 原因：macOS 自动下载系统更新 27.0.1（约 9.6G，`AutomaticDownload`/`AutomaticallyInstallMacOSUpdates` 均开）。04:39 开始下载时余量约 11G，04:47 写满，scheduler（当时空闲）与四个 im-session 报 `disk is full`/`Errno 28` 退出，04:51 更新准备失败释放空间；此后无调度，09:50 关机重启。10:35 系统再次下载并于 10:47 完成，余量由 20G 降至约 6.6G。项目告警不检查磁盘余量；Web 与 scheduler 开机不自启。自动安装可能再次在空闲时重启机器，需用户在系统设置关闭或择时手动更新。
- 恢复：`ops-restart-check.py preflight --release-id boot-20261001` 通过（在途 0、unknown 投递 0、requestRef 49,910 唯一、无存活进程）；`launchctl submit` 重挂 `io.bdhub.agent.web`（构建 `0Bv92chKmTQx2swXcBYB8` 未重建，日志 `var/web.log`），`start_scheduler`（`3184`）按原授权拉起 13 个常驻进程；postflight 除"角色与基线一致"（基线为开机后 0 进程）外全部通过。
- 清理（未停业务，余量 6.6G→12.4G）：移除 19 个提交已在 HEAD 的 worktree（分支保留；未提交的文档草稿归档在 `../BDHub-Agent-backups/worktree-archive-20261001/`），保留未合入的 `sdk-probe`；9 月中旬一次性快照库 8 个与较旧 Web 构建 13 个归档到 `../BDHub-Agent-backups/var-archive-20261001/`（哈希与 `quick_check` 校验后删除，Web 构建保留最近 3 个）；`state-retention` 只执行文件类候选（17 个旧身份代次、24 个采集 JSON、9,386 个阶段日志）到 `../BDHub-Agent-backups/retention/20261001T025222Z/`，两个大日志因仍有写入者跳过。货盘快照与来源轮次的库内删行留待 worker 空闲窗口；当前身份 profile 内浏览器缓存（每代约 0.5G）未动。

## 2026-09-29 21:30：审查 b798911 修复上线，意大利持续发送故障修复

- 依据 `BDHub-Agent_审查_b798911.md`：用户确认按建议推进，R02 保留"原意图首次后最多重放 2 次"只改文档，R03 维持既有"未决 ≤3 冻结、其余继续"不改，R04 只加覆盖标记。`42c0c9c`（R01 回读须总佣金一致含同活动号、缺值不确认；R05 类目重读后旧补洞不作证据；R06 读不懂页重试额度按类目；R07 调低平台槽容量不超配；R08 历史恢复用同一判定；R09 status/restore-isolated 不再 prepare；R10 台账不可读为未知并告警；R12 人工告警按会话分类器计达人；R14 策略读不到时等待原因待核实；R04 `coverageVerified/coverageDrift`），`ae247ae`（R11 总览仅全部市场读到才显示无事项、回复分项缺失不按 0 相加；R13 线索队列坏响应结束加载；各市场自己的数据时间与选入暂停说明），`37b4288`（新增只读 `scripts/ops-restart-check.py`）。
- 故障：09-29 约 16:29 起 IT 持续发送进程每次启动报 `review_limit` 退出（ready 517 + queued 2,199 超过复检单次 2,000 上限），重启 1,377 次零发送，上次成功为 09-28 22:40；告警未覆盖（状态文件停在等待窗口）。`37b4288` 发送与 IT 批量发送只取池顶 2,000 个位置复检。重启后 21:24 恢复发送。UK/BR/MY 发送路径不受影响。
- 部署：21:21 在线备份 `20260929T132106Z-pre-audit-b798911`（valid，restorable），`20260928T102458Z-pre-clean-once-20260928` 移入废纸篓。首次用 `ops-restart-check.py`：preflight 基线（requestRef 44,974、13 进程）→ 停 scheduler 后 drained 通过（在途含会话意图为 0）→ 停 worker 后 `--workers-stopped` 通过 → 重启（scheduler `18258`）→ postflight 全部通过（控制摘要不变、基线 requestRef 映射未替换且唯一、quick_check、13 进程载入 `37b4288`、角色一致、四会话 ready）。Web 构建 `0Bv92chKmTQx2swXcBYB8` 逐文件校验 286 个文件后切换，旧构建在 `var/web-releases/audit-b798911-*/previous`；页面与接口均 200，告警人工数与总控一致。

## 2026-09-28 23:45：选入被平台验证打断时暂停、已选商品继续下游

- UK 21:29 run 选入遇平台验证未通过（`commerce_verification_required`）停在 needs_human，已选商品卡在货盘阶段无法进入建链。用户确认只做 B（不降并发）：`2f4f649` 选入被平台验证中断（验证失败/超时，或未决 PID 全部为验证拦截或已明确受理待回读）时暂停选入而非停阶段，已选 PID 继续同步、建链与下游，scope 记 `selectionPaused`；台账仍有未提交 PID 时 selected 来源在上次完成后 3 小时再到期。登录故障、模糊回执、台账不可读或不全仍 needs_human。
- 22:29 UK run（旧代码）已把选入跑完：confirmed 10,653（当日 +2,343）、pending 0、未决 0、按规则 skipped 30；23:44 货盘阶段完成，进入 TapLink 建链。
- 22:47 在线备份 `20260928T144722Z-pre-selection-pause-20260928`（valid，restorable），`20260928T091458Z-pre-basis-points-20260928` 移入废纸篓；scheduler 等 UK 货盘阶段收口后于 23:44 退出，在途清零后重启（scheduler `37203`，13/13 载入 `2f4f649`），控制七表哈希不变，requestRef 40,216 唯一，`quick_check=ok`，四会话 owner ready。

## 2026-09-28 20:45：选入认证拒绝认可刷新代次

- UK 18:30 run：验证拦截项按 `--reconcile-rejections` 重提后新增 96（confirmed 9,805），4 个两次拦截按规则 `skipped_unknown`（不再提交），剩 854 待选；1 个 18:22 `16201010` 原意图因规则只认 relogin 代次，而调度自动恢复做的是 refresh，run 转 needs_human。用户确认两项：20:40 经账号页接口为 acc4 再次重登（`uk-acc4-relogin-20260928-selection-auth`，完成、收信重连）；`67d70bb` 选入认证拒绝在尝试后有 relogin 或 refresh 代次发布即可按原 PID 重提一次。
- 20:42 在线备份 `20260928T124246Z-pre-auth-refresh-20260928`（valid，restorable），`20260928T090500Z-pre-subcampaign-20260928` 移入废纸篓；完整重启（在途清零后停 worker），scheduler `17948`，13/13 载入 `67d70bb`，控制七表哈希不变，requestRef 39,872 唯一，`quick_check=ok`，四会话 owner ready。UK 下一轮 21:24 到期。

## 2026-09-28 18:35：英国 acc4 重登、选入推进与维护日只清理一次

- 用户确认后经账号页接口为 UK acc4 发起重登（`uk-acc4-relogin-20260928-login-expired`），17:53 用保存凭据自动完成、收信重连。只读复核：115 个隔离项全部恢复 confirmed、99 个未知中 92 个 confirmed（总佣金一致）；7 个为选入时平台验证拦截且回读不在池。14:04 原 run 自动续跑货盘：新增选入 1,192（confirmed 9,709），剩 924 待选；又有 30 个验证拦截未入池、1 个瞬时 `16201010`（随即只读探测 acc4 正常），超容忍转 needs_human。验证拦截且确证不在池的，下一轮货盘按既有 `--reconcile-rejections` 重提一次。
- 用户确认：`02336e0`+`2eef343` 维护日同一市场已有一次成功 `taplink_clean` 后，当天后续 run 跳过清理（此前 UK 当天 4 次重跑各约 3 小时）。18:24 在线备份 `20260928T102458Z-pre-clean-once-20260928`（valid，restorable），`20260927T090100Z-pre-platform-pool-20260927` 移入废纸篓；完整重启（在途含会话意图清零后才停 worker），scheduler `1894`，13/13 载入 `2eef343`，控制七表哈希不变，requestRef 37,400 唯一，`quick_check=ok`。随后 UK 新 run 清理 skipped、直接进入货盘。

## 2026-09-28 17:20：全托选入认可平台子活动，英国 acc4 登录失效

- 09-28 04:30 起 IT/UK 选入：平台把选入商品挂到其新建的全托子活动（活动号与列表不同），09-24 起的“只认冻结活动”规则把 IT 新 8 个、UK 207 个判为活动失配/未知（UK 超容忍 3 个转 needs_human，当天 4 次重跑各先跑约 3 小时 TapLink 清理）。核对历史 8,900+ 次确认选入：活动号全部不同、总佣金全部相同（公开佣金 15 个被调为 10%）。用户确认：`b67e6f6`+`92fca92` 回读在已选池、全托子活动（type 8/9）且总佣金（基点）与提交时一致即 confirmed（`platformAssignedCampaign`），新增 `select-global-products.py restore-isolated --confirm-restore-isolated` 只读复核隔离项。
- 两次完整重启（scheduler `92434`，13/13 载入 `92fca92`；备份 `20260928T090500Z-pre-subcampaign-20260928`、`20260928T091458Z-pre-basis-points-20260928`，旧备份移入废纸篓，保留 3 份），控制七表哈希不变，requestRef 35,620 唯一，`quick_check=ok`。第一次停 worker 时 UK 一条投递文本已 accepted（有 messageId）未确认，重启后按原 messageId 确认，无重发；此后停 worker 前等在途（含会话意图）清零，不为零则不停。
- IT 10 个隔离项（今日 8 + 09-23 用户选择隔离的 2 个）只读复核总佣金 15% 等一致，恢复为 confirmed（保留 `restoredFromIsolation`），零平台写入；待下次货盘/建链阶段进入 TapLink。
- UK 供给账号 acc4 自 14:04 后登录失效：已选池读取返回 code `16201010`，16:59 货盘阶段 `taplink_remote_read_failed`；UK 214 个待复核与 2,143 个待选入、TapLink、OECID 均依赖 acc4，需重登后继续。UK 收发信（acc11）不受影响。
- 48h 巡检：四市场 SDK 收信正常（接收延迟中位 2–3 秒，部分经 HTTP 回补），AI 回复正常；BR 1,485 个 PID 的 A/B 线索已全部查完、按 7 天周期 10-02 再查，主链滚动因无到期工作暂停（09-29 07:00 Campaign 刷新）；MY 09-26 货盘 `nested_step_exit_None` 已于当晚修复后正常。

## 2026-09-27 17:15：平台重型阶段并行上限可配置并设为 2

- 用户确认：`a34b5be` 把 `platform:global` 容量从代码常量改为 `operations-policy.json.platformMaxParallelMarkets`（1–2），生产设为 2；账号槽不变（同一账号不并用），借槽取回按同一容量。依据：09-27 IT/UK 两路类目读取与 OECID 同时访问平台约 1 小时、全程验证 0、拒绝 0。
- 17:01 在线备份 `var/backups/state/20260927T090100Z-pre-platform-pool-20260927`（33 库，valid，restorable）；`20260927T021433Z-pre-window-drift-20260927` 移入废纸篓，保留 11:32、14:49、17:01 三份。因策略文件新增键会被旧代码拒绝，本次先停 scheduler 与全部 worker，再合入并启动（scheduler `14762`，13/13 载入 `a34b5be`），控制七表哈希不变，requestRef 27,968 唯一，`quick_check=ok`，四会话 owner ready。
- 流程缺口：停 worker 时 MY 发送 worker 正在“打开会话”（`cycle_conversation_intent` inflight，卡片/文本仍 ready，按 part 检查为 0），投递 `delivery-bce1857557319cc0c327c43ab8f98015` 转 unknown，MY 二发暂停；重启后按原账号原请求核验 1,000 会话未匹配，17:14 结为 `quarantined_unknown`，未重发，MY 随即恢复发送。此后停发送 worker 前同时等待会话意图 inflight 清零。

## 2026-09-27 16:35：英国全托类目读取完成

- 英国 `uk-global-20260927-291b2d832f2b4c516f26` 30/30 类完成，96,460 商品（09-21 部分快照 64,089），已发布为 head。特殊完成：女装和内衣 `endpoint_end_window_rank_drift_2`、美妆个护 `endpoint_end_rank_drift_5`、时尚配饰 `endpoint_end_rank_drift_1`、家居装修 `endpoint_end_total_drift`、运动与户外/五金工具 `endpoint_end_reconciled`。按现门槛合格 10,355，新增合格候选 2,360（均未在池），待 09-28 04:30 UK 货盘阶段选入。
- 并行读取观察：IT/UK 两路类目读取与调度器 OECID 同时访问平台约 1 小时（00:49–01:45），英国单路读取至 16:34，全程验证 0、平台拒绝 0，主链无失败阶段；中断均为类目数据变动（总数漂移、排序漂移、空末页），与并行无关。

## 2026-09-27 15:05：类目空末页按空页处理，英国续读

- 英国“家居装修”第 193 页（末页）平台返回 `has_more=false` 且无商品列表，旧解析只对总数 0 放行，判 `page_shape_invalid` 挂起整轮。用户确认改法：`fe7c844` 末页明确无更多且无商品列表时按空页处理，完整性仍由总数与唯一 PID 判定；新增 `--retry-invalid-page` 按原页重读（每页最多 2 次）。
- 14:49 在线备份 `var/backups/state/20260927T064915Z-pre-null-tail-20260927`（33 库，valid，restorable）；`20260926T173648Z-pre-total-drift-20260927` 移入废纸篓，保留 10:14、11:32、14:49 三份。完整流程重启：scheduler 等 UK OECID 收口后退出（15:01），恢复 scheduler `96224`，13/13 载入 `fe7c844`，控制七表哈希不变，requestRef 26,886 唯一，`quick_check=ok`，四会话 owner ready。重读该页得空页、总数已降为 2,879，已取 2,880 唯一 PID，按总数漂移规则完成；英国继续读取其余类目。

## 2026-09-27 11:45：排序漂移放宽到所有类目

- 英国“美妆个护”（6,960，非窗口类目）跨页重复 7 行，补洞 3 次复读 436 页仅补回 2 个，第 4 次全量复读时平台总数变化被拒；用户确认把排序漂移规则扩到所有类目：`4a009c0` 补洞已复读全部重复页仍未补齐、差额不超过 `max(5,total/1000)` 时记完成（`endpoint_end_rank_drift_N`），“女装和内衣”原记录 `endpoint_end_window_rank_drift_2` 保留。
- 11:32 在线备份 `var/backups/state/20260927T033225Z-pre-rank-drift-20260927`（33 库，valid，restorable）；`20260926T163544Z-pre-acct-count-20260927` 移入废纸篓，保留 01:36、10:14、11:32 三份。完整流程重启（scheduler `67568`，13/13 载入 `4a009c0`），控制七表哈希不变，requestRef 26,886 唯一，`quick_check=ok`，四会话 owner ready。“美妆个护”结算为 `endpoint_end_rank_drift_5`（6,955/6,960），英国继续读取其余类目。

## 2026-09-27 10:20：10,000 窗口类目排序漂移判完成，英国续读

- 意大利 01:45 完成（`it-global-20260927-a5e6432c7c7b5bc409df`，28/28 类、31,830 商品，head 已更新）；按现门槛新增合格 19 个，其中 8 个未在池，待 09-28 04:30 货盘阶段选入。
- 英国“女装和内衣”（展示窗口 10,000 行）读得 9,998 唯一 PID：2 页跨页重复，补洞复读 42 页无新增，但重复页复读内容不同（分页中重新排序），旧规则拒绝稳定重复。用户确认放宽：`f4a9bae` 对 reported total 等于 10,000 窗口的类目，最近一次补洞已复读全部重复页且无新增、差额不超过 `max(5,total/1000)` 时记完成（`endpoint_end_window_rank_drift_N`）。
- 10:14 在线备份 `var/backups/state/20260927T021433Z-pre-window-drift-20260927`（33 库，valid，restorable）；`20260926T152552Z-pre-overview-restart-20260926` 移入废纸篓，保留 00:35、01:36、10:14 三份。完整流程重启（scheduler `53458`，13/13 载入 `f4a9bae`），控制七表哈希不变，requestRef 26,886 唯一，`quick_check=ok`，四会话 owner ready。英国以 `--accept-stable-duplicates` 结算该类目（`endpoint_end_window_rank_drift_2`）后继续读取其余类目。

## 2026-09-27 01:40：类目总数漂移判完成与英国全托全量重读

- 00:49 起意大利（acc9，run `it-global-20260927-a5e6432c7c7b5bc409df`）与英国（acc4，新 run `uk-global-20260927-291b2d832f2b4c516f26`，原 09-21 run 为用户接受的部分结果 13/30 类，不改其终态）全托一级类目只读读取同时进行，用于检验多市场并行平台读取；期间无验证、无拒绝，主链阶段正常。
- 英国“家纺布艺”两次读取中平台总数持续下降（3,783→3,777，商品下架），唯一 PID 等于末页总数仍被旧规则判 `category_endpoint_total_mismatch`，整轮停止。用户确认改规则：`4f60f47` 读取中总数变化且唯一 PID 不少于末页总数记完成（`endpoint_end_total_drift`，逐页总数留证），少于仍不完整。
- 01:36 在线备份 `var/backups/state/20260926T173648Z-pre-total-drift-20260927`（33 库，valid，restorable）；`20260926T134155Z-pre-outcomes-restart-20260926` 移入废纸篓，保留 23:25、00:35、01:36 三份。完整流程重启（scheduler `85666`，13/13 载入 `4f60f47`），控制七表哈希不变，requestRef 26,886 唯一，`quick_check=ok`，四会话 owner ready。意大利读取进程优雅停止后以原 run 续读，英国以 `--retry-partial-category` 重读该类目后继续。

## 2026-09-27 00:45：总览“账号需人工”口径修正与意大利全托类目重扫

- `a3a295c`：经营总览“账号需人工处理”改与账号页同一规则——某账号的 needs_human 维护意图只要之后有更新的维护意图即视为已被取代。原计数把 BR acc2、IT acc6/acc9、UK acc4 已被后续成功维护取代的 5 条旧意图算进来；修正后四市场均为 0（生产只读核对）。MY acc8 最近 failed_known `NameError` 为 09-24 已修复缺陷（`a8e6d94`），新代次已发布，无需处理。
- 00:35 在线备份 `var/backups/state/20260926T163544Z-pre-acct-count-20260927`（33 库，valid，restorable）；`20260926T125331Z-pre-sender-fix-restart-20260926` 移入废纸篓，现保留 21:41、23:25、00:35 三份。完整流程重启：scheduler 等 IT OECID 收口、claim 清零；停 AI/发送 worker 与会话 owner，无在途投递/回复后恢复（scheduler `72929`），13/13 进程载入 `a3a295c`。控制七表哈希不变，requestRef 26,886 唯一，`quick_check=ok`，收信 gap 0，无版本混用告警。
- 00:40 按用户要求用 `global-source-control.py` 显式启动意大利全托一级类目只读重扫（run `it-global-20260927-a5e6432c7c7b5bc409df`，acc9）；上次完整类目发现为 09-21（31,809 商品）。新合格商品的选入仍由主链货盘阶段执行（IT 下一次 selected 到期 09-28 04:30）。

## 2026-09-27 00:25：会话中我方消息也可翻译（仅 Web）

- `4873bc4`：会话时间线中我方发出的消息（机构后台、人工、AI、主动邀请）也显示“翻译成中文”，复用原翻译接口，只调用模型、无平台写入。构建 `RFWphfGerY6JazDkJRp3w` 逐文件校验 286 个文件后切换，旧构建 `4OockedwlUJBitQxK7-v8` 在 `var/web-releases/translate-*/previous`；四市场页面、会话接口、总控与告警均 200，浏览器只读确认 IT 会话我方气泡显示按钮。后台进程未重启。
- 用户决定不做：“首次达到可发条件”指标、告警同根因合并、指标明细下钻接口（对项目不重要）。

## 2026-09-27 00:20：告警分组与总览运行详情（仅 Web）

- worktree `../BDHub-Agent-pm2-20260927`（分支 `codex/pm-followups-20260927`）实现后合入并推送 `main`（`d629488`）。告警条按“系统问题 / 需要人工处理 / 正常等待与提示”分组，等待类（平台拒绝已自动暂停、未知发送已隔离、收信人工停止、生产暂停）不计入紧急数；选品隔离与 AI 首轮完成需人决定，归人工。经营总览“最近运行”可展开只读详情（起止、耗时、项数、写入证据、原因代码、运行编号）。达人页累计指标改名“累计有回复/累计加橱窗”；不支持全托管的市场货盘默认打开 Campaign。
- 只发布 Web：构建 `4OockedwlUJBitQxK7-v8` 逐文件校验 286 个文件后切换，旧构建 `vKiVYIncz227GBM9PFInC` 在 `var/web-releases/followups-*/previous`；根路径 307，四市场 20 项页面及概览、总控、告警接口均 200。scheduler 与常驻 worker 未重启，无运行版本混用告警。

## 2026-09-26 23:30：审计 I01–I08 修复与经营总览上线

- 依据 `e4bb4a5` 只读审查与前端重设计报告，在 worktree `../BDHub-Agent-pm-20260927`（分支 `codex/pm-redesign-20260927`）实现后合入并推送 GitHub `main`（`a831d8d`）。代码：模型探测每次领取带独立令牌（I01）；总控人工队列复用会话分类器、工单数单列（I02）；线索队列排队时也轮询、失败有终态、不覆盖未保存草稿（I03）；会话队列未读到时显示“—”（I04）；资源需求算不出时显示“原因待核实”（I05）；线索后续文案收窄（I06）；阶段卡显示单位与窗口、总控通道带单位/范围（I07）；拒绝闸门与 G13/G14 文档改为当前事实（I08）；每日统计新增来信达人（按 OECID 去重）与已确认服务回复的人工/AI 分项。前端：根路径进入“经营总览”（今日各市场矩阵、需要我处理、供给是否够用、运行详情收起），导航“总控制台→经营总览”“运营首页→市场运营”；会话队列按“待我处理/进行中/历史”分组、资料默认收起、删无效图片按钮；发送页先显示当前状态、模板收起；作业时间标注“全市场共享/仅当前市场”。
- 23:25 在线备份 `var/backups/state/20260926T152552Z-pre-overview-restart-20260926`（33 库，valid，演练 restorable）；按“保留最新 3 份”将 `20260926T114550Z-pre-console-restart-20260926` 移入废纸篓，现保留 20:53、21:41、23:25 三份。
- 按原流程重启：scheduler 等 MY OECID、UK 发送池在途阶段收口后退出、claim 清零；停 AI/发送 worker 与会话 owner，无在途投递/回复后 `start_scheduler` 恢复（scheduler `59481`），13 个常驻进程载入 `a831d8d`、无未提交运行代码。控制/授权七表哈希不变；requestRef 26,866 唯一；`quick_check=ok`；四市场会话 owner ready、收信 gap 0。
- Web：构建 `vKiVYIncz227GBM9PFInC` 逐文件校验 289 个文件后切换，旧构建 `Y8bpOpMzlNZzJ1zcU9py1` 在 `var/web-releases/overview-*/previous`；根路径 307 到 `/it/console`，四市场 36 项页面/接口及总控、告警接口均 200，总控人工队列与会话页一致（BR 7、IT 6、MY 3、UK 2）；切换前后常驻进程不变。
- 未做：告警条同根因合并（需后端稳定根因标识）、达人/货盘页小幅精简、任意指标的明细下钻接口、“首次达到可发条件”（历史台账无法补造）。

## 2026-09-26 21:50：线索后续归因与会话列表提速上线；长阶段中途让槽评估后撤回

- 用户确认后合入并推送 GitHub `main`（`3bd7024`）。上线：线索页“近 7 天发布的线索后来怎样”（A/B 分开，按投递冻结的来源计发出、触达与卡片后回复，属观察期内相关统计）；会话列表改由 SQLite `json_extract` 读取投递回执 messageId，生产备份上 4 市场×6 视图结果与旧实现一致，单次读取约减半。
- “长阶段在两轮之间让出平台读取槽”已实现后经用户讨论撤回（`35a8d10` revert）：下午的长时间排队根因是 MY 货盘误判循环重跑（已修），正常情况下长阶段频率低、被挡住的 OECID 晚一段时间进发送池对当天发送影响很小，而中途暂停会拉长长任务并引入新的失败方式。保留已上线的“等待账号恢复时让出平台槽”。
- 21:41 在线备份 `var/backups/state/20260926T134155Z-pre-outcomes-restart-20260926`（33 库，valid，演练 restorable）；按“保留最新 3 份”将 `20260926T072629Z-pre-r3b-restart-20260926` 移入废纸篓，现保留 19:45、20:53、21:41 三份。
- 按原流程重启：scheduler 等 UK OECID 在途阶段收口后退出、claim 清零；停 AI/发送 worker 与会话 owner，无在途投递/回复后 `start_scheduler` 恢复（scheduler `39160`），13 个常驻进程载入 `3bd7024`、无未提交运行代码。控制/授权七表哈希不变；requestRef 26,860 唯一；`quick_check=ok`；四市场会话 owner ready、收信 gap 0。MY/UK 发送处于 `waiting_capacity`，告警显示当日平台拒绝新联系（发送量较大后的当日额度，与部署无关）。
- Web：构建 `Y8bpOpMzlNZzJ1zcU9py1` 逐文件校验 289 个文件后切换，旧构建 `0_3YEjKiE3FMWr2tEGPBE` 在 `var/web-releases/outcomes-*/previous`；四市场 32 项页面/接口及总控、告警接口均 200，切换前后常驻进程不变。

## 2026-09-26 21:10：MY 发送停机修复、认证等待让槽、模型熔断全覆盖、Kalodata 贡献上线

- 用户确认后合入并推送 GitHub `main`（`051dc47`）。内容：BR/MY/UK 发送在卡片派发前被本地判定为单个收件人终态（如货盘刷新后冻结报价失效 `offer_changed`）时只取消该投递未提交组件并继续，不再让整个市场 worker 停在 attention（今天 MY 货盘刷新后出现 4 次，每次停到该投递 30 分钟到期）；等待账号恢复的阶段把 `platform:global` 借给其他市场；会话翻译、商品短名、素材准备接入模型服务熔断；每次 A/B 发布记录新增达人×商品、刷新组合与全新达人，线索页“最近一次抓取”显示；总控补“已停止，需关注”文案。
- MY 货盘 20:16–20:43 正常完成并发布（16,803 项），误判修复生效。随后 MY 首次建链（`taplink_prepare`）于 20:53 开始；用户说明 MY 此前已建过大量链接，要求打断。21:04 核实其处于读取阶段（`catalog-link-prepare.py read`）、阶段写入 0、近 30 分钟无任何建链意图后，对该进程组发 SIGTERM；阶段记为失败（`taplink-read-campaign_report_invalid`，写入证据保守记 uncertain），不会自动重跑，未产生链接意图。
- 20:53 在线备份 `var/backups/state/20260926T125331Z-pre-sender-fix-restart-20260926`（33 库，valid，演练 restorable）；按“保留最新 3 份”将 `20260926T061807Z-pre-round3-restart-20260926` 移入废纸篓，现保留 15:26、19:45、20:53 三份。
- 按原流程重启：scheduler 退出、claim 清零；停 AI/发送 worker 与四个会话 owner，无在途投递/回复后 `start_scheduler` 恢复（scheduler `30797`），13 个常驻进程均载入 `051dc47`。控制/授权七表哈希不变；requestRef 26,538 唯一；`quick_check=ok`；四市场会话 owner ready、收信 gap 0。MY 卡住的投递 `delivery-559f3083…` 由新逻辑结算为 `offer_changed`、平台写入 0，MY 发送随即恢复并确认新投递。
- Web：构建 `0_3YEjKiE3FMWr2tEGPBE` 逐文件校验 289 个文件后切换，旧构建 `s9RlPjPiMHuy3fsk9LJyQ` 在 `var/web-releases/sender-fix-20260926T130701Z/previous`；四市场 32 项页面/接口及总控、告警接口均 200，切换前后常驻进程不变。

## 2026-09-26 20:00：审计 H01–H16 与总控制台上线

- 依据 `b1d16cc` 只读审计，在 worktree `../BDHub-Agent-h-20260926`（分支 `codex/console-audit-20260926`）实现后，用户确认合入并推送 GitHub `main`（`a527f68`）。内容：旧回执重放不再启动进程；租约过期后结果照常结算、被回收的迟到结果存证、写阶段过期转人工核对；模型熔断按调用代次；时间线复合游标下推；Kalodata 状态区分排队/运行并给出最近一次抓取；线索状态只读；首页类型化指标、供给主链与持续通道分组；运行代码未提交改动告警；恢复演练逐意图核对冻结身份；活跃日志不做复制截断；市场轮流领取主链资源；只读总控制台 `/<市场>/console`。H11 增量读模型、H13 净增量/后续归因、认证等待让出平台槽与阶段切片未做。
- 部署前发现：第三轮加入的嵌套失败检测把 `campaign-collect` 报告里的逐轮进度 `steps`（`status: paused`）当成失败子步骤，MY 货盘 15:53 起 6 次实际完成（如 19:10 一轮 16,803 条 offer、4,865 个合格 PID）却记为 `nested_step_exit_None`，每轮约 26 分钟占住平台读取槽，BR/IT/UK 的 OECID 因此长时间排队；货盘从未发布，MY 发送 worker 因 `offer_changed` 停在 attention。本次一并修复（只把带 label/exitCode/result 的条目当子步骤）。这些失败 run 已被自动转为 `needs_human`（`workflow_retry_requires_review`），作为历史保留未改写；Campaign 采集本身零平台写入。
- 19:45 在线备份 `var/backups/state/20260926T114550Z-pre-console-restart-20260926`（33 库，valid，演练 restorable，8 条二发意图无冻结身份记为待核实）；按“保留最新 3 份”将 `20260926T043827Z-post-followup-release-20260926` 移入废纸篓，现保留 14:18、15:26、19:45 三份。
- 按原流程重启：SIGTERM scheduler 后等 MY 货盘在途阶段自然结束（19:55，这一轮是真实的 `taplink_remote_read_failed`），claim 清零；停 AI/发送 worker 与四个会话 owner，无在途投递/回复、无残留后 `start_scheduler` 恢复（scheduler `19140`），各常驻进程载入 `a527f68`、运行代码无未提交改动。控制/授权七表哈希不变；requestRef 25,522 唯一（较 19:46 增加的是发送窗口内的正常发送）；`quick_check=ok`；四市场会话 owner ready、收信 gap 0。MY 发送 worker 仍因 `offer_changed` 周期性停在 attention，待 MY 货盘成功发布后应自行恢复。
- Web：worktree 构建 `s9RlPjPiMHuy3fsk9LJyQ` 逐文件校验 289 个文件后切换，旧构建 `S33VU3T7Rm0laxo8dzm0x` 在 `var/web-releases/console-audit-20260926T115743Z/previous`；四市场首页、总控、会话、货盘、运行证据及各接口共 34 项均 200，总控只读打开无脚本错误，可见各市场 OECID 按轮次领取平台槽；切换前后常驻进程 PID 不变。

## 2026-09-26 15:30：第三轮后续（G15、G17、G18、G21）上线

- 用户确认后从 worktree `../BDHub-Agent-r3b-20260926`（分支 `codex/round3-followups-20260926`）快进合入并推送 GitHub `main`（`4875710`）；G13/G14 按用户要求暂缓。内容：认证失效统一经 `login_recovery.request_recovery`，同市场/账号/代次/错误族只有一条先刷新后重登的维护意图；`state-backup.py drill` 离线恢复演练；会话队列游标翻页、表结构单次读取、`inbox_checkpoint_oec` 索引、回复投影增量分块；运行证据页、未知状态显示“状态待核实”；版本告警改为比较 `scripts`/`vendor` 代码树（此前 14:30 的纯文档提交曾让告警条误报 13 个进程版本不同）。
- 行为变化：选入/OECID 发现登录失效时先刷新再重登（原来直接重登）；同一代次恢复失败后不再每轮重试，等人处理或定期维护发布新代次。
- 15:26 在线备份 `var/backups/state/20260926T072629Z-pre-r3b-restart-20260926`（33 库，valid），恢复演练 restorable；按“保留最新 3 份”将 `20260926T033201Z-pre-review-fix-restart-20260926` 移入废纸篓，现保留 12:38、14:18、15:26 三份。
- 按原流程重启：SIGTERM scheduler，IT OECID 在途阶段 20 秒内收口、claim 清零；停 AI/发送 worker 与四个会话 owner，无在途回复/投递、无残留后 `start_scheduler` 恢复（scheduler `78752`），13 个常驻进程均载入 `4875710`。控制/授权七表哈希不变，19,436 个 requestRef 唯一不变，`quick_check=ok`；新索引已由收信进程创建；四市场会话 owner ready、收信 gap 0；没有新增账号维护意图；版本误报消失。
- Web：worktree 构建 `S33VU3T7Rm0laxo8dzm0x` 逐文件校验 279 个文件后切换，旧构建 `AnaTYSXor9lB1mMxA3faS` 在 `var/web-releases/r3b-followups-20260926T072932Z/previous`；四市场页面、会话、技术挂起、运行证据、首页与概览接口均 200，运行证据 13/13 为当前代码；IT 队列游标第二页 100 条无重复；浏览器只读打开无脚本错误；切换前后常驻进程 PID 不变。

## 2026-09-26 14:30：第三轮审查（G01–G12、G16、G19、G20）上线

- 依据用户提供的 `f864218` 第三轮只读审查，在 worktree `../BDHub-Agent-round3-20260926`（分支 `codex/round3-review-20260926`）实现后，用户确认合入并推送 GitHub `main`（`90a373a`）。内容：非零写入阶段必须有显式写入证据，失败保留嵌套摘要；全托选入无进展/进展不明停止；SDK 回填只在读到保存链顶消息后续读；人工发送命令持久登记（未交接超时收口、晚到原请求被拒）、被动收信凭精确消息证据确认回复；模型服务熔断（连续 3 次服务级失败暂停 300 秒、退避上限 1 小时、单探针），未发出的调用不计次；首页保存回执与乱序读取保护；常驻进程登记加载提交（`var/runtime-loaded/`），版本混跑告警；备份前检查空间。G13/G14 按用户要求暂缓。
- 14:18 在线备份 `var/backups/state/20260926T061807Z-pre-round3-restart-20260926`（33 库，valid）；按“只保留最新 3 个备份”，`20260924T035449Z-pre-retention-20260924` 移入废纸篓，现保留 09-26 11:32、12:38、14:18 三份。
- 按原流程重启：SIGTERM scheduler 待在途阶段完成、claim 清零，停 AI/发送 worker 与四个会话 owner，确认无在途投递/回复、无残留后 `start_scheduler` 恢复（scheduler `69434`），由其按原持久授权拉起 13 个常驻进程，全部登记加载 `90a373a`。重启后四市场会话 owner ready、收信 completed、gap 0；AI 窗外或正常完成；scheduler 已跑 UK/BR 轮次无错误；控制/授权七表哈希与重启前一致，19,436 个 requestRef 唯一不变，主库 `quick_check=ok`；无版本混跑或模型暂停告警。
- Web：worktree 构建 `AnaTYSXor9lB1mMxA3faS`（build/typecheck 通过）逐文件 SHA-256 核对 279 个文件后切换，旧构建 `M3ovMuGEsvm0eoohmYlwH` 在 `var/web-releases/round3-review-20260926T062708Z/previous`。验收中曾把浏览器标签页长期累积的请求记录误判为循环请求而短暂回退，复测（模拟可见）确认每 20 秒约 3 次读取、首页与会话页正常、无脚本错误后重新切回同一已核对构建。四市场页面/会话/技术挂起/首页/概览及告警接口均 200。

## 2026-09-26 12:40：在线备份修复与发布后完整备份

- 用户清倒废纸篓后磁盘可用约 20GB。原在线备份按 2048 页分步复制，其他连接每次提交都会使其重启，业务运行时永远做不完（此前只能停机备份）；`110ddc6` 改为单个读事务内一步复制，回归测试以持续写入复现旧问题（旧代码 60 秒内未完成，新代码约 1 秒）。
- 不停业务完成 `var/backups/state/20260926T043827Z-post-followup-release-20260926`（33 库，22 秒，verify valid）。按用户“只保留最新 3 个备份”，最旧的 `20260923T164039Z-pre-storage-cleanup` 移入废纸篓；现保留 09-24 11:54、09-26 11:32、09-26 12:38 三份。常驻 13 个进程、scheduler 与四市场收信在备份前后均正常。

## 2026-09-26 中午：第二轮审查（F01–F12、D01）修复上线

- 依据用户提供的 `0d9f39d` 复核审查，在 worktree `../BDHub-Agent-followup-20260926`（分支 `codex/followup-review-20260926`）修复后，用户确认合入并推送 GitHub `main`（`093f04d`）。内容：AI 回复 900 秒期限只看提交时刻；`service_reply` 懒加列串行化；会话“技术挂起”队列、隔离/未决回复只读核验、时间线分页、发送失败区分确定未提交与未知、草稿按会话保留、详情随列表刷新、列表集合预筛（生产快照结果一致，UK 单次约 4.1s→0.12s）；首页保存回执与状态不可确认提示；TapLink 按保存星期执行；单页读取不再产生新 gap，`scripts/inbox-gap.py` 诊断/有证据恢复存量 gap（生产当前 0）。
- 验证：Python 1,515、Web 187 通过，build/typecheck 通过；仅离线测试与只读 SQLite。
- 12:20–12:35 按原流程重启常驻进程：SIGTERM scheduler 待 UK OECID 在途阶段自然完成、claim 清零；停四市场 AI/发送 worker 与会话 owner，确认无在途回复/投递、无残留。**磁盘仅约 1.3GB 可用，静止备份因 `database or disk is full` 失败**（无残留临时目录）；本轮无库结构变更，最近可用恢复点仍为 11:32 的 `20260926T033201Z-pre-review-fix-restart-20260926`。随后 `start_scheduler` 恢复（PID `53012`），由其按原持久授权拉起全部 worker；无 stop/pause 标记。
- 重启后四市场 SDK 收信 `completed`、gap/backfilling 0，发送 worker 等待窗口，AI 窗外/正常完成，scheduler 无错误；控制/授权哈希与重启前一致，19,436 个 requestRef 不变，主库 `quick_check=ok`。
- Web：worktree 构建 `M3ovMuGEsvm0eoohmYlwH` 逐文件 SHA-256 核对后切换，旧构建 `gO-lvIJ1kdedvqM_h2bgH` 移至 `var/web-releases/followup-review-20260926T042718Z/previous`（同目录 `release.json`），只 kickstart `io.bdhub.agent.web`（新监听 PID 53434）；四市场页面/会话/首页/技术挂起队列/概览及告警接口均 200，浏览器只读打开会话页、首页、作业页无脚本错误；切换前后 13 个常驻 worker PID 一致。
- 磁盘清理（用户要求“只保留最新 3 个备份”）：保留并校验 `var/backups/state` 中 `20260926T033201Z-pre-review-fix-restart-20260926`、`20260924T035449Z-pre-retention-20260924`、`20260923T164039Z-pre-storage-cleanup`（各 33 库，valid）；其余 29 项完整/单库备份、`var/backups/20260920-*` 6 个目录及 `var/releases/*` 中全部发布前数据库快照（约 20GB，均早于 11:32 完整备份）移入废纸篓 `~/.Trash/BDHub-Agent-old-backups-20260926`，发布回执、日志、git bundle 与旧 Web 构建保留。清倒废纸篓后空间才释放，由用户执行。SDK 上线报告所述“三库在线备份”已不在原位，回退仍按报告保留当前库，不恢复旧快照。

## 2026-09-26：外部静态审查 R1–R10 修复合入生产

- 依据用户提供的 `acad472` 只读审查（GPT 静态审查），逐项核对代码后在独立 worktree `../BDHub-Agent-review-20260926`（分支 `codex/review-fixes-20260926`）修复，用户确认后快进合入本机生产分支并推送 GitHub `main`。
- 内容：阶段写入证据（`writeEvidence`，写入类阶段仅确认零写才自动重排）；IT OECID 有界切片归一化；OECID 等待绑定实际执行账号；TapLink 标准卡须平台有效/商品合格；AI 回复冻结原发送身份、最多 3 次只读核验（≥120 秒、900 秒预算）后按达人隔离；SDK 收信断点续读（`backfilling`）；全托 ≤3 个未决 PID 时只冻结该 PID、Campaign 继续；仅平台额度回执暂停当日新联系（同类非额度拒绝 3 次按系统性拒绝暂停）；BR/MY/UK 每周一只读 binding 检查（不删除）。参数按用户要求保持默认。
- 验证：Python 全量 1,503、Web 180 通过，build/typecheck 通过。仅离线测试与只读 SQLite 查询；未调用平台、未写生产库、未改配置或授权。
- 11:30 左右按 [上线报告](../implementation/sdk-http-rollout-20260926.md) 原流程重启常驻进程：SIGTERM scheduler `98148`，等 UK OECID 在途阶段自然完成、claim 清零；再停四市场 AI/发送 worker 与四个会话 owner，确认无在途回复/投递组件、无本项目残留浏览器。库静止后备份 `var/backups/state/20260926T033201Z-pre-review-fix-restart-20260926`（33 库，valid）；运行中在线备份因 second-cycle 持续写入反复重启而中止，其临时目录已删除。随后 `start_scheduler` 恢复（scheduler `46164`），由其按原持久授权拉起四个 owner、IT 发送与四市场 AI、BR/MY/UK 发送；无 stop/pause 标记需保留。
- 重启后核对：四市场 SDK 收信 `completed`、gap/backfilling 均 0；发送 worker 等待窗口，AI 窗外或正常完成；scheduler 无错误并以新代码运行 IT/UK 阶段。自动运营、发送控制及请求、AI 设置、回复配置、控制事件、账号运行设置哈希与重启前一致；19,436 个 requestRef 唯一不变，`service_reply` 已追加发送身份/核验列，主库 `quick_check=ok`。Web 未重建重启（发送面板新状态文案待下次 Web 发布生效）。`service_reply` 新列由新代码首次实例化 `AutoReplies` 时原地追加；`inbox_backfill` 由 `Inbox` 自建，均为 additive。
- 已知生产影响：IT 选品台账现有 1 个 `result_unknown`，新 scheduler 生效后 IT catalog 会绕开该 PID 继续选入（产生新的选入写入）；旧的无写入证据失败写入阶段不再自动重排而转 needs_human；BR/MY/UK 周一新增只读平台读取。

## 2026-09-26：仓库文档与已发布代码同步

- 本次只按 `d3bcc5a` 静态核对文档，未重新采样生产健康或业务数量；以下 03:41 及更早条目保留各自时点。历史条目的“未启用”“待实施”不覆盖后续发布记录。
- 本机生产分支 `codex/v1-runtime-alignment`；GitHub 私有仓库 [1435762425/BDHub-Agent](https://github.com/1435762425/BDHub-Agent) 的正式分支为 `main`，同步前远端与本机同为 `d3bcc5a`。`pre-sdk-rollout-20260926` 和 `sdk-http-four-market-20260926` 已上传，保持原发布指向。
- 修正 README 的 B 类市场范围、Web 测试环境旧说明，以及 Campaign/全托/二发/概览的过时实施标记；模块方案和 AI 审查新增当前状态表，旧源码行号明确为历史审查依据。
- 未完成项明确保留：全托选入剩余 unresolved 分支、IT 供给 OECID 的 `_call` 计数/等待归一化、平台拒绝与额度的精确分型、AI 模型故障分类/unknown 隔离/新入站服务、长期记忆和统一账号恢复。没有因修改文档将目标当成已上线能力。
- 本次没有修改代码、业务配置、指南、授权或本机 `var/`，没有操作业务进程或调用平台；仅文档链接/锚点与差异检查。私密配置、真实状态、身份文件和备份继续留在 Git 之外。

## 2026-09-26 凌晨：四市场 SDK 收信与原 HTTP 发信已切换

- 用户明确要求直接上线四市场并做好 Git 备份；生产原未提交文档先原样提交 `ba3b6d0`。回退标签 `pre-sdk-rollout-20260926`、同名备份分支（`codex/` 前缀）及已验证 bundle 保留；三库在线备份、生产副本迁移幂等与旧表不变检查完成。详见[上线与回退报告](../implementation/sdk-http-rollout-20260926.md)，私密证据 `var/releases/sdk-http-rollout-20260926/`。
- 主实现 `1c43f95`，运行修复至 `34dcb23`，从独立 worktree `../BDHub-Agent-sdk-rollout-20260926` 合入。IT acc6/acc9、BR acc1/acc2、MY acc8/acc5、UK acc11/acc4：前者 SDK 收信＋原 HTTP 二发/AI，后者供给＋OECID。schema v26 只追加持久唤醒；原账号、请求和消息台账不重建。
- 四供给账号真实 Find 通过；IT/UK Profile type2 通过，BR/MY 该额外接口业务码 100000，未授予画像能力。IT/BR/UK 主链已出现新供给账号成功事件；MY 主链关闭状态保留，只有只读 canary，不称其自动主链已运行。
- 初启旧非文本回放误排唤醒，已按原权威消息精确收口且未改业务状态；旧探针把 SDK 初始化中 1 误当就绪，正常成功 2 后触发约 80 秒重连。核对平台枚举并修复，四市场已正常运行满约 600 秒并完成续期、重新就绪；32 次周期采样中 IT 1 次、其余各 2 次在交接短暂借用不可用，随后全部自动恢复。初始化期间 HTTP 继续服务，没有新的 sdk_disconnected。
- 原 scheduler 自然完成在途身份阶段后恢复 PID `98148`；会话 owner IT/BR/MY/UK 为 `97786/97788/97790/97792`，原发送/AI worker 继续。四市场页面和概览均 200，Web 未重建重载，旧项目未修改。
- Python 全量 1,474、Node 180 通过；末次只加目标消息补查回归后 owner 专项 15 通过，既有 ResourceWarning 保留。9,718 个投递快照、19,436 个 requestRef、8 个旧隔离投递、控制/授权/指南和八账号 headers 一致，主库 quick_check=ok。
- 本次 scheduler 首次恢复后原 HTTP AI 确认 BR4/UK2，无未结 AI unknown；IT窗外、MY未到窗口。SDK通知有回查证据，但四市场自然新来信实时延迟及正常二发窗口的卡文吞吐尚无完整验收样本，不能用初始化回放代替。
- 回退优先保留当前代码/数据库，只切回 `http_polling` 与 `identityAccountRole=communications` 并按报告安全换 worker；旧代码不认识 v26，禁止盲目回旧代码并覆盖整库导致丢新来信。四市场 AI full 授权保持，未来 AI unknown 隔离优化仍属后续。

## 2026-09-26 凌晨：同账号 SDK/HTTP 前置共存验证通过，尚未切换生产

- 用户同意先验证原推荐架构：通讯账号SDK收信＋保留HTTP发信，另一账号货盘/OECID。实验 worktree `../BDHub-Agent-sdk-probe-20260926`、提交 `4969c7d` 未合入生产，详见[共存验证](../implementation/sdk-http-coexistence-probe-20260926.md)。
- MY/acc8 当前窗外且主链关闭，无在途消息，做两轮各约50秒的只读探针。持原账号锁，在同账号临时 Profile 副本启动SDK；IM身份与原HTTP匹配，两个回调挂载。原独立消费者抢锁被拒绝，负责人复用HTTP会话可读；临时副本已删除、锁释放，headers/身份代次不变。
- 每轮基线3对会话＋历史读取、SDK在线4对；中位数分别约1.00/1.01秒→0.91/0.92秒，不能当发信提速证明。第二轮原HTTP发送适配器走到最终许可被拒绝，POST0、sendRequests不变。没有真实测试消息或建会话，也没有消息写入生产台账。
- 第一轮1,511条回调全部是原库已有旧消息；第二轮按SDK消息时间无探针期间新消息。不能称捕获了上千新来信或已测实时延迟。上线必须处理启动回放、精确去重和四市场通知识别，不能直接复制旧文本正则。
- 2项锁测试＋55项IM协议测试通过。原MY收信PID44917已恢复completed，四市场收信当前无错误；正式worker、授权、代码、旧项目均未改动。证据 `var/research/sdk-http-coexistence-20260926/`。
- 下一步是会话负责人和单市场小批真实业务试点入口；真实卡文吞吐、确认率、新来信延迟、维护/断线和其他市场仍待验证。新执行器真实首发按页面明确启动；此探针不提供发送入口，不从技术共存推导已可全量迁移。

## 2026-09-26 凌晨：用户授权四市场 AI 全量，已开启并实际回复

- 用户认可两轮模拟，明确允许确认不与二发冲突后全部开启；经页面同一 resume-full API 为 IT/BR/MY/UK 保存 agent-v2-full-run，四市场均 enabled=true、rolloutStage=full。此前“等待用户开全量”已不再是当前状态。详见[开启记录](../implementation/agent-full-activation-20260926.md)。
- 实际二发均北京时间16:30–24:00；AI 为 IT 00:30–03:00、BR 00:30–08:00、MY 09:00–16:00、UK 00:30–04:00，全部满足30分钟缓冲。开启前无未结回复/在途二发门禁冲突，同账号共用写锁。窗口、指南、设置版本和二发控制未改，worker 未重启。
- 01:07 快照：本次授权后新回复确认 IT11/BR8/UK4，MY窗外等待09:00；同期新二发投递0、四 sender 等待窗口。UK 短暂 ProfileBusyError 后已继续；BR 也遇到身份任务账号占用、原 ready 保留后自动重试。不能把这种账号等待当作二发同时发送。
- 已明确说明现有异常边界：AI 回复结果未知仍可能使同市场二发等待核验；本次没有实施 §9.18 的有限核验隔离。所有原 unknown 保护保留，没有重发或改判结果。
- 证据 `var/releases/agent-full-run-20260926/activation.json` 含原设置、窗口检查、四次 API 回执/授权事件与实际送达计数。此次是真实启用后的业务回复，非模拟；没有修改代码、进程或旧项目。

## 2026-09-26 凌晨：用户认可首轮 AI 质量，第二轮 16 组对照已交付

- 用户认为首轮回复质量较高，不认可助理主观评级，要求再看16组；没有要求改变指南或开启 AI 全量。本轮不做质量分级，只列来信、模型实际原文、中文对照和系统校验结果。
- 固定随机种子 2026092602，四市场各4位；16位达人及原消息与首轮无重叠。沿用 revision 2 指南、DeepSeek flash、生产 live 上下文及校验器，临时数据库隔离，每组只调用一次；14组校验通过、2组未通过，全部保留原文，不重试挑答案。
- 更正首轮 MY-03：所谓“不存在的消息 ID”实际来自输入 showcaseEvidence 的加橱窗通知。是校验器仅接受 messages 数组引用导致拦截，不应归因于模型凭空引用。旧对照和交付报告已补正，原始模型证据未动。本轮 MY-R2-02 同类；MY-R2-04 是 reply 与 waitFor=clarification 的结构不匹配。未修改运行代码。
- 对照 `outputs/ai-reply-review-round2-20260926/report.md`；私密请求/响应/抽样依据在 `var/research/ai-real-message-round2-20260926/`。16次调用、48,106 token、估算 ¥0.0330064；生产指南、授权、回复/决策台账摘要未变，实际发送0，临时模拟库已删除，未操作进程。

## 2026-09-26 凌晨：UK 预检阻断收口，真实 AI 回复模拟已交付

- 用户选择先修 UK 阻断，再看真实来信模拟；代码 `93cb471` 从 `../BDHub-Agent-uk-review-20260926` 合入，见[交付报告](../implementation/uk-preflight-ai-simulation-20260926.md)。没有执行后续 AI 规则改造或开启全量。
- 原 acc11/CID 只读回查是一条空正文、身份角色无法按当前合同归类的旧消息；这不是本次卡文送达未知。非 IT 漏掉整套 ready 的预检终止处理，已确认会话又被过期清理遗漏，致使同一人反复占队头。新代码收口完全未提交的组件，保留历史、会话回执和请求；inflight/received/unknown 仍只核验。
- 主库备份/副本幂等演练后，在原 UK executor 锁内结束 `delivery-b3c37e75c6aa86810b5b9a419f60747a`：投递/卡/文 cancelled，会话 confirmed，原请求与快照不变；_unsettled 不再返回原队头，专项平台写入 0。当前发送窗外，UK waiting_window，无实际新触达验收。
- Python 1,458 项、6 项新增回归通过，既有 ResourceWarning 保留。四 sender `61715/61716/61717/61719` 载入新代码；scheduler 原 UK 身份阶段自然结束后恢复 `62510`，四 inbox/Agent 与 Web 未动。控制/授权/指南不变，AI 仍 pilot_complete_waiting_resume。
- 16 组真实来信（各市场4组）用临时库和当前 revision 2 指南、DeepSeek flash、生产同一上下文/校验器模拟；每组一次，保留失败原话，不调用 apply/transport。14 组结构通过、2 组被拦截；定性复核 5 可用/4 可改进/5 需修改/2 拦截。突出问题为售后错分、重复已做步骤、无依据反馈承诺、正文与路由不一致、引用不存在的 ID。暂不建议直接开全量。
- 用户对照文件 `outputs/ai-reply-review-20260926/report.md`，私密输入/原始响应在 `var/research/uk-preflight-ai-simulation-20260926/`。16 次模型调用，49,686 token，估算 ¥0.0431298；生产 decision/service_reply 未新增，实际发送 0，临时模拟库已删除。HTML 直接浏览器预览被策略阻止，提供 Markdown，不绕过限制。
- 发布证据 `var/releases/uk-preflight-20260926/`；原未提交文档保留。下一步待用户看当前回复后，按已有政策修场景/路由/引用与上下文遵循；不要先修改指南重跑来掩盖本轮问题。

## 2026-09-25 夜间：集中回复范围与队列已加载

- §9.14 代码 `670d3e8` 从 `../BDHub-Agent-reply-queue-20260925` 合入，见[集中回复交付](../implementation/reply-scope-queue-20260925.md)。按最早未处理来信排队，一份输入包含同达人全部当前问题，decision 冻结消息版本；apply/begin 复核，确认/no_reply 只结算覆盖范围，新来信/编辑保留且已答版本不重入队。
- schema v25 追加精确处理证据和查询索引；只从原 cursor 回填 40 条已处理版本，历史处理时间明确未知。真实副本原表摘要一致、重复迁移为空、quick_check=ok。生产一致备份后迁移约 56ms，无旧消息/意图重写。
- Python 全量 1,452、Node 180 通过；最后积压年龄调整后相关 17 项通过，新增范围测试 16 项，既有 ResourceWarning 保留。副本和测试零平台/模型调用；Web 无改动、未重建重载。
- 当前问题不受 24 条历史裁剪限制；live 用当前本地对话，默认目标时刻模式兼容。超预算 input_blocked 保留诉求、不调用模型、不占队头，未实现自动分段、长期提醒记忆及完整历史状态回放。非 IT 包装的确定未提交上下文拒绝可取消 ready，unknown 仍只核验。
- 先让 scheduler `44906` 的 UK 身份阶段自然完成/claim 释放，再停四 Agent 安全合入；新 scheduler `52659`，Agent `52662/52694/52704/52710`。四 inbox、IT/BR/MY sender 保持；UK 在合入前已 attention/unknown_message_needs_review，原监督器换 PID 后仍阻断，本批未绕过。旧项目和 Web 未改。
- 上线四市场会话 GET 约 0.79–1.15 秒（先前约 32–58 秒），概览/发送 GET 均 200；慢点为逐达人外发/橱窗查询缺索引。真实只读候选读取约 0.03–0.09 秒，BR 队首含 3 条未答消息，未实际生成或发送回复。
- 控制/授权/指南、旧快照/requestRef 不变，decision/service_reply 无新增；四市场仍 pilot_complete_waiting_resume、未开全量，MY 主链关闭。收信继续、scheduler 当前无错误。证据/备份 `var/releases/reply-queue-20260925/`，原未提交主文档保留。
- 下一批模型失败分型与回执、AI unknown 有限核验隔离、二发隔离后的新入站服务资格；长期业务记忆/严格回放继续补齐。UK 原消息阻断须按原账号/证据单独诊断，不能通过重发或改成功解决。

## 2026-09-25 夜间：原邀请遇新来信的继续规则已加载

- §9.13 代码 `ceca684` 从 `../BDHub-Agent-invitation-20260925` 合入，详见[邀请衔接交付](../implementation/invitation-inbound-20260925.md)。原卡已确认、文字从未提交时，只有同 CID 实时入站造成的连续版本变化可让原文字继续；pending/unread 保留，其他门禁不放宽，旧取消/隔离不复活。入站先落库而卡后确认未放宽。
- 首次收信基线改为同会话已确认卡的 started；真实 Agent 窄范围补入同套已确认邀请，默认 replay 不看未来，旧 episode/link 不改写。集中合并/排序、完整 live/replay 和 SDK 接收适配仍未完成。
- Python 1,435 项、Node 180 项通过；四市场生产副本演练通过、零平台/模型调用，新增写事务最长约 15.4ms。两次一致备份后迁移 v24，只加查询索引；131 张业务表摘要不变，重复迁移为空、quick_check=ok。既有 ResourceWarning 保留。
- scheduler 安全结束原 BR 身份阶段、claim 清空后停四 sender/inbox/Agent；无未隔离在途组件/AI 回复才迁移合入。23:23 恢复 scheduler `44906`，四 sender `44913/44916/44919/44922`、inbox `44910/44914/44917/44920`、Agent `44911/44915/44918/44921`。Web 未重载，旧项目未改。
- 控制/授权事件/指南不变，9,639 份旧快照、19,278 个 requestRef、191 个旧终结状态不变。四市场持久 AI pilot_complete、未开 full；瞬时 send_dispatch_active 不代表全量。MY 主链仍关闭，sender 等额度；正常收信/原授权发送继续，scheduler 当前无错误。
- 四市场概览/发送/会话共 12 个 GET 200；会话列表实测 32–58 秒，未做旧版对照，需下一批定位，不能宣称性能验收通过。BR/UK 真实完整邀请数从静止快照增长 9/13；新继续凭证仍 0，尚无目标交错的真实自然案例，一般发送增长不能代替新规则实测。
- 证据 `var/releases/invitation-inbound-20260925T145214Z/` 含备份、演练、测试和发布检查。原未提交主文档保留并补充本批边界。下一批按最早未处理来信的集中排序/合并与精确处理水位，再完善上下文和隔离服务；不重开已经确认的商业政策。

## 2026-09-25 夜间：A/B 发送份额已上线

- 用户要求继续下一批；`fd64aea` 实现持久 4 A / 1 B，`cb6ad41` 补同会话观察，均从 `../BDHub-Agent-outreach-20260925` 合入。详见[80/20 交付](../implementation/outreach-share-20260925.md)；原未提交方案文档保留。
- schema v23 只加轮转/份额表和索引，四库备份、重复迁移、旧 delivery/part/conversation intent 摘要检查通过；旧冻结/在途不回填。market×北京时间日×OEC 去重，游标跨日延续；新 delivery/两组件/份额同事务，失败回滚，回查不重复计数。
- 同人有合格 A 归 A，否则可用 B；两类内部择优，空侧借位且不积欠额。机构额度不足先筛已解锁/预留达人；窗口、72 小时冷却、来信/人工/隔离和逐写许可不改。安排与最终预检取消、确认、未知分开。
- 离线修复 IT 候选历史扫描（79 秒→约 2.5 秒）与 MY 额度空查材料（35–38 秒→约 0.37–0.52 秒）。副本实际冻结/replay 幂等、零平台写入；最长写事务约 0.96 秒，未在事务内请求平台。
- Python 全量 1,417 项、Node 180 项、build/typecheck 通过；既有 ResourceWarning 保留。四市场发送池 GET 200，页面显示今日安排/补位/结果；卡确认后同会话 72 小时内回复、加橱窗按达人观察，不当因果归因。
- 安全点切换 scheduler `26232`，IT/BR/MY/UK sender `26249/26252/26255/26258`；四 inbox `14505/73852/73855/73858` 和四 Agent `72227/72231/72234/72237` 未变。Web build `gO-lvIJ1kdedvqM_h2bgH`，观察显示补充仅重载 Web。
- IT 真实前十新领取 `AAAABAAAAB`，前八个 A 最终历史预检取消、B 确认，不称十位都触达。后续同一快照：IT 安排 87A/53B、整套确认44A/52B、A取消43/B在途1；BR 3A/39B 均确认；UK 116A 确认；MY 0，额度等待。B 补位 IT25/BR31，A 补位 UK23；这是供给差异，不凑成功比例。
- 控制/授权/指南和旧快照/requestRef 校验一致，scheduler 无错误，MY 主链仍关闭、AI 均 pilot_complete_waiting_resume。证据 `var/releases/outreach-share-20260925T133752Z/release.json`，包含备份、回退 Web、测试与实际业务快照。
- 下一批：§9.13 原卡确认后遇入站仍发完冻结文字，并保留未回答来信；随后集中回复和 AI 上下文。最终历史预检取消的 A 候选可另评估资格前移，本批没有绕过门禁。

## 2026-09-25 晚间：身份技术预算、成功子集交接与锁回归恢复

- 用户要求开始身份阶段；主实现 `8f14f83`，修复 `5417d30/ec9f2c7/3ef575c/1dde00f` 已从 `../BDHub-Agent-identity-20260925` 合入，见[身份预算报告](../implementation/identity-budget-20260925.md)。原未提交方案文档保留。
- schema v22 追加 handle 预算/事件/账号等待表，三库一致备份、重复迁移与回填演练通过。按原报告回填 794 条共享故障和 2,823 条健康 Find，未制造个体隔离；1 份不可完整验证报告单列。旧混合用途 attempt_no 不当作个体失败次数。
- 单市场/规范化 handle 累计 3 次个体失败后隔离，退避 5/30 分钟；账号/共享故障按 1/15/60 分钟等待，不消耗达人预算、不建人工案件。健康 Find 或新代次恢复通道，个体预算不重置。IT 保留原 cohort/lease/QPS，其他市场持久开始事件、原报告恢复与消费锁。
- IT 每段至多 4×50/200 次领取，其他市场 50 位/每批 3 位；成功子集先交接，剩余如实保留，到期可独立从 OECID 续跑。Profile 失败不丢 Find；A/B 身份计数统一 handle 判定并单列隔离。
- 首次切换出现 IT 旧 handoff 在写事务中慢扫当前来源，导致数据库锁等待；IT inbox 及 IT/BR/UK sender 退出。停止 scheduler 并终止只做本地交接的 submit 后回滚未提交事务；共享来源读取改到写事务外、materialize，写前复核 heads/handoff。副本交接 0.08 秒、最长写锁 7ms；身份统计 75 秒慢展开也修到两组合计 0.31 秒。
- 恢复 worker：IT inbox `14505`，IT/BR/UK sender `14506/14508/14509`；MY sender `3530`、其他三市场 inbox `73852/73855/73858`、四 Agent `72227/72231/72234/72237` 保持原进程。scheduler `14910`，Web build `YIlVVsnOFNFzgkTkV-EkJ`。不能称为全程业务进程未变。
- 原零写入失败轮次 `workflow-742c332ba5913ca4a7ed1160f341` 按原阶段与 upstream 保存恢复 checkpoint 后续跑；IT 首段 200 handle：found 172、notFound 28、pending 360→160，发送池已发布。BR/UK 也完成有界身份阶段并衔接发送池，仍有原初始化等共享等待；MY 主链保持关闭。
- 最终全量 Python 1,402 项、Node 179 项、build/typecheck 通过；既有 ResourceWarning 保留。身份 GET 200、约 0.61 秒、行/handle 分项一致，四市场概览 GET 200。最后 scheduler 无锁错，恢复 worker 存活，收信扫描正常；控制/授权/指南/原隔离发送冻结证据摘要一致，AI 仍 pilot_complete_waiting_resume。
- 证据/三库备份/旧 Web：`var/releases/identity-budget-20260925T124004Z/`。回退消费者须停身份消费或保留新预算门禁，不能恢复旧整库抹去新增消息/身份。
- 下一批：80/20 发送机会分配，然后原卡文邀请遇新来信的完成例外、AI 集中回复与上下文；本批没有开启 AI 全量或改商业回复规则。

## 2026-09-25 晚间：二发未知有限核验和技术隔离

- 用户要求继续新计划；本批先完成二发未知隔离。独立 worktree `../BDHub-Agent-isolation-20260925`，代码 `e5521e1`、说明 `9833201` 已合入；原未提交方案文档保留。统一身份技术失败预算仍待下一批，不能把本批称为全部模块完成。
- 四市场正式 sender 共用原意图有限只读核验。建会话 2 轮/每轮 180 秒/最多 101 页，卡/文各 3 轮/每轮 90 秒；轮间至少 300 秒，首轮起总期限 900 秒，读取前持久计数。两次成功历史缺失的旧卡片标准保留；失败读取不算缺失。
- 耗尽只隔离主动营销，保留原组件状态，不创建技术人工案件、不改变关系/拒联/历史商业案件。新执行锁覆盖原请求及收口；迟到确认不重开投递，核验确认卡片与关闭未提交文字同事务；发送池与状态接口独立展示技术隔离。
- 全量 Python 1,384 项通过，后增两项账号与四市场只读合同测试、相关 12 项通过；Node 178 项、build/typecheck 通过。主库一致备份/quick_check 和真实 UK 意图副本演练通过，无新 schema 迁移；保留既有测试 ResourceWarning。
- scheduler 与三条原运行 sender 安全退出后重载（IT 等当前投递收口），四个 inbox 和四个 Agent PID 保持。Web 首次提交未正常监听，重新提交恢复；四市场发送池 GET 200。所有控制、授权和指南摘要一致，四市场 AI 仍 pilot_complete_waiting_resume，MY 主链仍关闭。
- UK 原 unknown `delivery-c9a59543a01769ed361dfa2bcd54cc10` 经原 acc11 两轮各 101 页/1,000 会话，只读预算耗尽后自动隔离；不把列表缺失或匹配当原请求回执。原请求/快照/卡文组件摘要不变，case 数 0，专项平台写入 0。截至约 20:13，新 sender 已向其他 9 位达人完成卡＋文字并回读确认（UK confirmed 1,553→1,562），原未知意图没有重发。
- 证据 `var/releases/delivery-isolation-20260925T120210Z/release.json`，备份 `before.sqlite`，报告 [二发未知隔离](../implementation/delivery-isolation-20260925.md)。回退旧 sender 必须保留新营销隔离门禁或先停发送，旧代码依赖 human 关系会遗漏新隔离；不得回灌旧库覆盖新增收信。
- 后续优先 §9.8：market×规范化 handle 持久身份技术预算，区分账号/共享运行时故障，不让成功子集等整批；然后 80/20 发送、原邀请遇入站的完成例外、AI 新上下文/队列规则。

## 2026-09-25 晚间：A50/B 四市场滚动队列已加载并修复运行回归

- 用户要求开始下一批。独立 worktree `../BDHub-Agent-leads-20260925` 验证后合入 `13ee781`，后续修复 `d9afd89/a643f2b/ba2d2f4`；详见 [滚动队列交付](../implementation/rolling-market-leads-20260925.md)。A50 独立新范围，B 四市场隔离，原窗口/分页/轮转持久化；3 请求一片段、8 片段一阶段，已完成 PID 先交接，配额按市场等待。
- schema v21 在五库备份后应用；原 B 1,942 条投影、182 个 head、4,039 条作者缓存及不可变 run/evidence 校验一致，旧 IT 三表保留为 legacy。重复迁移无新增。初始每类范围 IT 2,736 / BR 1,480 / MY 3,047 / UK 9,962。
- 真只读接入验证 12 请求，四市场列表及作者均通过、身份文件未改。Python 最终全量 1,373 项、Web 178 项、build/typecheck 通过。没有平台测试发送、AI 全量授权或未知重发。
- 初次约 19:00 重载 scheduler、三个运行 sender、四个 Agent 及 Web。后发现身份投影长 SQLite 写锁，四个收信 worker 被原监督器自动重启；不是承诺中的“全程 PID 未变”，已明确记录回归。`a643f2b` 增量/幂等、100 项短事务修复，真实备份副本 200 边最大写锁约 1ms。
- `d9afd89` 让被更新完整结果覆盖的旧 B 断点本地 supersede、不再请求；`ba2d2f4` 正确接受身份 nothing_pending，并使原身份失败不堵读取。UK 原零写入错误阶段按原 run 记录恢复 checkpoint 后重排。原 identity_queue_stalled/report_invalid 等技术等待仍单独保留，不冒充已解决。
- 当前 scheduler `80618`，Web listener `72253`、build `_FImgNrvahFKTkbQNrZlO`。收信 `73849/73852/73855/73858` 在 19:25–19:31 核对期间无新增锁错、PID 稳定、状态无错误；四市场 Agent 仍 pilot_complete_waiting_resume。业务控制/指南与原未知发送摘要不变。
- 19:31 A50 已发布 IT 276、BR 146、UK 189 个 PID 查询，IT/BR 已有 50 位结果；B 当前投影 IT 2,005 / BR 39 / UK 9（含历史，不当新增）。MY 总开关保持关闭、没有自动读取。四市场队列接口 200，UK 页面 A/B/额度/隔离/身份等待显示正常。已开启市场依原授权继续，业务发送不计作本轮测试。
- 证据、五库备份、旧 Web：`var/releases/rolling-leads-20260925T105701Z/`。旧代码不能直接无 market 过滤读取新 B 投影，不能用旧整库覆盖新收信。
- 下一批仍需统一身份技术失败隔离、二发 80/20 和发送 unknown 有限核验；UK 原建会话 unknown 仍未重发，真实发送仍受其阻断。新队列上线不代表这些政策已经执行。原未提交方案文档继续保留。

## 2026-09-25 下午：全托累计候选与 15 天发现已加载

- `c1a304f`、`141d3d3` 在独立 worktree 验证后合入，详见 [全托交付](../implementation/fullmanaged-incremental-discovery-20260925.md)。IT/UK 首次/15 天按类目发现，周度材料维护复用已发布来源；不恢复历史 stopped 任务。原 fullCatalogWeeklyEnabled 字段与授权保留，页面改为全托商品发现。
- source 新增累计候选/发布记录，selection 新增原意图 owner/member 引用；新轮不覆盖资格、不复制未知、不重排已知失败。当前推广检查不重新筛销量/评分；新入池评分 0 不当无评分，旧资格不改判。
- 四库备份后增量迁移，回填 IT 2,333、UK 8,341 个历史候选（不是新扫描新增）；9,563 条原 intake 行及所有原表哈希不变。重复迁移新增 0，quick_check=ok；临时副本 prepare 继续引用原任务，不改旧行。
- Python 全量 1,352 项、展示补充 3 项、Web 177 项及 build/typecheck 通过。16:50 换 scheduler `51866` 与 Web，16:55 Web 展示补修后 listener `52597`、build `m-yPr1G9zYulZG5X4nki2`。其余 11 个业务 worker PID 不变，控制/授权/配置摘要一致，AI 四市场仍 pilot_complete_waiting_resume。
- 四市场货盘/作业/概览/首页 HTTP 200；IT 当前发布货盘 completed 与最后采集 stopped 分开，UK accepted_partial 如实保留。下一次发现按来源时间到期：IT 10-06 05:56、UK 10-06 16:11（北京）；旧故障/暂停不会被到期绕过。未主动执行新平台扫描/选入/发送，旧项目未改。
- 发布前英国 sender `76463` 已因 market_send_conversation_result_unknown 停在 attention，不在本次 11 个运行 worker 中；原建会话意图未重放。IT 既有 Kalodata read_failed、池空等断点也未自动重排。下一批 A50/B 滚动队列与身份衔接；新发送隔离仍待实施。
- 证据/四库备份/旧 Web：`var/releases/fullmanaged-20260925T084852Z/`。原有未提交方案文档保留，未整体代为提交。

## 2026-09-25 下午：Campaign 连续性与有限核验已加载

- 用户要求按新方案持续推进。`2018b7c` 在独立 worktree 验证后快进合入：新申请普通未知不截断其他活动/下一批，旧已加入采集独立；确认材料先准备，再共享最多 4 轮/300 秒只读核验，耗尽停止跟进且不重发/不建人工事项。最终范围仍属于原 catalog generation，详情见 [Campaign 交付](../implementation/campaign-continuity-20260925.md)。
- 四个 Campaign 库备份后新增五列，973 条原记录及原字段未变；临时副本重复迁移/quick_check 通过，历史请求 JSON 不伪造。状态只读，无库不创建；未知活动缺席不误退役其原绑定。
- Python 1,340 项、Web 176 项、build/typecheck、文档与 diff 检查通过。修复 MY 累计 842 条活动触发页面 400 条上限；展示区分有限核验与停止跟进，不要求用户逐项查。
- 16:19 核对无 claim/在途主链/Campaign 子进程后，仅换 scheduler（`46046`）与 Web（listener `46073`，build `lzuNcEdG1PaRiduck1_sf`）；另外 12 个业务 worker 原 PID 保留。控制/授权/指南摘要一致，四市场 AI 仍 `pilot_complete_waiting_resume`，没有全量授权变化。
- 四市场 Campaign/概览/首页 HTTP 200，IT Campaign 页面正常。未主动发起平台申请/发送/模型调用；生产当前无 Campaign unknown，真实新政策案例尚未发生。证据及四库备份、旧 Web 位于 `var/releases/campaign-20260925T081751Z/`。
- 下一批：全托首次/15 天按类目发现与累计候选，再接 A50/B 四市场滚动队列。现有 IT Kalodata 旧失败、历史账号事项等未在本批自动重排；不把 Campaign 上线当作其他模块已完成。原有未提交方案文档继续保留，不代为整体提交。

## 2026-09-25 下午：调度/B 类连续性与 Agent 执行修复已加载

- 按用户“按照新计划持续推进”执行两个独立小批次，详见[交付报告](../implementation/continuity-agent-runtime-20260925.md)。旧项目未修改，原有未提交方案文档保留；没有 schema 迁移或重建任务。
- `fb30003` scheduler 常驻模式在长任务中补领就绪阶段、每轮刷新状态/续租，阶段发布事务内校验 fence；`62630b7` B 新代次保留旧投影，每 PID 的回执/head/current/job 同事务发布，兼容旧中断，并在池/候选/逐写前检查 30 天视频窗口。全量 1,318 项通过；生产库临时副本零网络回放通过。
- 15:26 核对无主链 claim/在途阶段、IT sender 等待窗口且无在途/未知投递后，仅重载 scheduler 和 IT sender，新 PID `36586`、`36591`；其他 11 个业务进程未动，持久控制/授权摘要不变。证据 `var/releases/continuity-20260925T072608Z/release.json`。
- `7041651` 移除 Jev adapter/新执行入口及配置样例，历史分类/评测和消息 backfill 保留，本机私密 TypeSafe 配置未读/未删；`1bb0c4a` Agent 的 case/锁/确认意图/decision 关联原子提交，准备首份可发回复即执行，不再积压 20 份草稿，有进展时短间隔继续、等待和失败仍退避。全量 1,320 项通过。
- 15:42 确认无 ready/inflight/accepted/unknown 服务回复后重载四个 Agent；其余 9 个业务进程不变。四市场均保持 pilot_complete_waiting_resume，生产模型/回复行数未增加，设置、授权与已发布指南哈希一致；没有开启 AI 全量。证据 `var/releases/reply-cleanup-20260925T074208Z/release.json`。
- 四市场概览 GET 回读正常；本轮没有主动平台写入或模型调用。下一批仍需完成 Campaign 有限核验、全托增量候选及 A50/B 四市场滚动队列等；AI 的 live/replay 上下文、处理范围和新隔离政策也未因本批修复自动上线。用户已允许必要进程切换，后续继续先核对相关进程/锁/断点，不重复询问已定业务规则。

## 2026-09-25 下午：首批只读运营概览已发布

- 用户在主体模块规则逐项确认后要求开始开发；首批限定为四市场只读运营面板与统计口径。代码在独立 worktree `../BDHub-Agent-ops-overview-20260925` 验证后合入，提交 `7b812bf`、`86e31b4`；原有未提交方案文档保留，没有代为提交。
- Python 1,305 项、Web 174 项测试通过，Web build/typecheck 与桌面/390px 显示检查通过。新 GET `/api/market-overview?market=<market>` 区分当前库存、身份来源行/去重账号名、7 日事件、等待、技术隔离和真实人工事项；未实现数据不冒充 0。
- 用户随后明确“可以动进程，继续”：北京时间 14:47 仅切换 `io.bdhub.agent.web`，原端口 5198。新 build `LJr-fYqji0wCeI4Cd_vFb`，旧构建保留在 `var/web-releases/readonly-overview-20260925T064754Z/previous`，同目录 release.json 记录校验和回读。
- 四市场新概览 GET、原首页 GET 与页面均 200；生产浏览器四市场渲染无脚本错误，桌面/手机无页面级横向溢出。切换前后记录的 13 个业务 worker PID 一致；二发控制、AI 设置及授权事件摘要一致。没有平台/模型调用、迁移、业务控制变更或业务 worker 重启。
- 只上线第一批展示与口径，不代表 A50、B 四市场、15 天全托探查、80/20 发送或新的自动隔离已实施。运行证据和回退说明见[首批交付](../implementation/readonly-operations-overview-20260925.md)；后续按[模块方案](../implementation/module-optimization-plan-20260925.md) §10 接续，不重新询问已定业务规则。

## 2026-09-25 上午：审查、DeepSeek 分工与修复

- 分工（用户要求）：Claude 写任务并审阅，deepseek-flash（关闭 thinking）写代码，工具 `~/.config/deepseek/ds_patch.py`（密钥同目录，0600，不入 Git）；改动先在 worktree 审阅再测试、合入。不再批量调用 agent。
- 16 个 Codex 未提交文件经审查后原样提交（`89d6349`、`0644375`、`4b8f0f4`、`5a9c62d`），内容未变，生产目录已无未提交改动。
- 修复：`32f28ad` Kalodata 视频列表翻页移动导致同一视频重复写入崩溃（IT 07:00 轮次 07:48 起 kalodata-video-run_report_invalid）；`6e478bf` 退役失败回退为阻断、同轮已发布货源只认对应且唯一的 head、`selected_source_run_id` 统一运行号；`3d0c2fb` OECID 探针初始化失败（未发请求）等 5 秒重试一次并记录脱敏 errorMessage；`e1e2411` `resume-after-fix`；`f3f3c29` TECHNICAL 补齐；`4407195` 跨活动选品两次相隔 5 分钟以上回读即自动 `isolated_unverified`（用户决定），告警条提示近 7 天隔离数。
- 10:30 `resume-after-fix`（`resume-uk-oecid-after-fix-20260925`）重排 UK 09-24 轮次 OECID，10:37 运行中。IT 09-25 07:00 campaign 轮次 kalodata 已失败 2 次，10:48 第 3 次自动重试（新代码）；若仍失败，用 `resume-after-fix` 续跑。
- 待用户：四市场 AI 首轮试点已完成，需页面核对后开启全量；建会话无回执是否自动核验隔离；IT B 类作者是否送 OECID 解析。

## 2026-09-25 凌晨：IT AI 回复空转、收信自动刷新与 UK OECID

- 00:31–01:50 IT Agent 每约 68 秒生成一条回复（模型调用＋acc6 会话读取），发送时被 `begin()` 以 `delivery_unknown` 拒绝，适配器报成 `ItalyImDeliveryError`；约 60 条来信各消耗 1 次重试（1 小时后可重试，最多 3 次），0 条发出。原因：09-24 23:14 投递 `delivery-57a00ac79feb92df465b125a991fa189` 的建会话请求没有回执，停在 unknown；IT 二发也从 23:13:56 起停发到窗口结束，只读核验路径无法结清无回执的建会话。
- 01:45:43 用 job-run 停 IT Agent，5 秒后被 scheduler 重新拉起，停止无效。01:48–01:52 只读完整扫描 acc6 会话列表：101 页、1,000 个会话（平台列表上限，条目无时间），该达人 0 个；01:52:24 用现有 `quarantine_unknown_conversation`（`quarantine-it-20260925-create-unknown`）隔离：投递 quarantined_unknown，卡片和文字仍未开始，达人转人工，开 `conversation_create_unknown` case。01:52:30 下一轮回复发出并回读确认，IT 首轮试点完成。
- `189ee2f`：Agent 在窗口内调用模型前先查 `reply_blocker`（与 `begin` 同条件），有 unknown 投递或在途回复时返回 `waiting_dispatch`；unknown 告警写明结清前二发和 AI 回复都暂停。
- `635c300`（用户选择“自动刷新一次”）：收信认证遇 16201010 时为当前身份代次登记一次 refresh 并拉起维护 worker。生产库副本演练：四市场各登记一次，重复调用只读回同一意图，副本已删除。02:01:16 核对无在途回复、投递和维护后，SIGTERM 8 个收信/Agent worker；scheduler 02:01:23 以新代码拉起，四市场收信正常。
- AI 首轮试点：IT、BR、UK 均为 `pilot_complete_waiting_resume`，等待用户在页面核对后开启全量；MY 09:00 开窗。
- UK 09-24 07:00 轮次续跑后：Kalodata 以 UK 自己的额度读 A 类，23:33 `quota_exhausted`（1,947 项）。OECID 期间 acc11 00:34 遇 16201010，被 OECID 路径自动重登（00:35 新代次）。探针在 23:34、00:35、01:36 三次于初始化阶段 `RuntimeError`（约 40 毫秒、未发请求），都紧跟 2–3 次间隔 1–2 秒的成功 pass；报告 `identityFileUnchanged` 为空，被判 `market_identity_report_invalid`，run failed 并每小时自动重试（最多 3 次，之后转人工，会挡住 UK 09-26 的主链）。推测是上一轮签名/验证码运行时未释放；涉及的 `market_identity.py`、`probe-italy-profile.py`、`operations_scheduler.py` 都在未提交改动中，未修改。
- 建会话无回执的自动核验未实现：会话列表只返回最近 1,000 个且条目无时间，自动隔离需约定证据标准（例如建会话后若干小时内完成的完整扫描），待用户决定。

## 2026-09-24 夜间：IT 原轮次续跑与账号掉线（用户选择）

- 用户选择续跑 IT 原轮次：两个活动对不上的 PID（尾号 77284、73153）保留全部证据但本轮不用；今晚跑完 TapLink、Kalodata、OECID、发送池。
- `a2fa4c2` 恢复工具支持 `isolatePids`，设置检查改为两个开关都开且 revision 不低于原轮。22:05:23 打开 IT 自动运营（revision 6）；22:05:47 `resume-selected-recovery`（`resume-it-catalog-20260924`）：两项改 `isolated_unverified`，重复轮次以 `superseded_duplicate_run` 停止，原轮次 catalog 重排。
- 货盘 22:05–22:09：复用已发布 source `it-global-20260923-0537c0e0577f`，同步已选池 3,411 个商品，平台写入仍为累计 3。TapLink 22:09–22:12 完成，无需新建链接。
- Kalodata 22:12：A 类队列为空；B 类新一代 `video-generation-397fc2254cddfab450b33441`（窗口 08-24～09-22，2,879 个商品）。初始化按已知缺陷清空了上一代 343 条 B 类投影。22:13:52 在视频 `7684553680099478816`（商品 1729482676927110125）详情缺作者处整体停下，run needs_human，与 09-23 20:30 那轮同一条视频。
- `e14eaeb`：缺作者的单条视频记 `author_missing`（保留响应 hash），该 PID 的 run 记 `completed_with_gaps`，其余视频照常进投影；同一代超过 20 条才停下。22:18:24 `resume-video-author-skip`（`resume-it-video-author-20260924`）后扫描在原代次断点继续，已跳过该视频。Kalodata 阶段在当日额度耗尽（`quota_exhausted` 算成功）或扫完时结束，随后 OECID、发送池。
- UK 09-24 07:00 主链轮次 `workflow-0ca7578e9ab618dc401cb49302f7`：货盘（16 次写入）、TapLink（682 次写入，1,907 个商品）完成，08:08 Kalodata 因共用登录失效（`kalodata_auth_required`）转人工。当前轮次 needs_human 时调度器跳过该市场，UK 09-26 也不会开新轮。`ea464e4` 新增 `resume-kalodata-auth`：停下之后已有 Kalodata 页面读取成功才允许一次性重排。
- BR 收信约 22:15 起报 `taplink_remote_read_failed`：只读诊断为 acc1 机构信息接口返回业务码 16201010（登录失效，非验证码/风控；UK acc11 同接口正常）。acc1 身份为 09-21 22:15 重登，UK acc11 已 84 小时仍正常，不是固定 72 小时过期。22:42:47 账号页接口 refresh，回退重登，22:45:17 发布 `identity-generation-3b804d336e46aaa0961002ae`，无需人工验证；22:45:41 BR 收信恢复（20 个会话、5 条新回复）。收信路径遇 16201010 不会自动重登（OECID 路径会），待用户决定。
- 平台额度：MY 22:13:27 第 1,000 张卡后同样收到 `im_limit_reached`（状态 3、check_code 100）。22:46 当天确认卡片 BR 1000、MY 1000、UK 1001、IT 918；约每市场每天 1,000 个新联系已由三个市场实测。
- 22:22 发送池（可发/待解析 OECID）：IT 291/1,358，BR 229/68，MY 2,888/541，UK 4,518/812。明天 BR、IT 受货源限制：BR 靠 09-25 07:00 主链，IT 靠本轮 OECID。
- IT 原轮次 22:52:57 完成：视频扫描 77 个商品、2,101 次请求后 IT 当日额度耗尽（`paused_quota`），B 类 1,278 条（58 个商品，清空前 343 条）；OECID 本轮开始时队列只有 27 个待办（找到 18、找不到 9，`backlog_clear`）；发送池发布 5,360 个位置。22:53 IT 可发 266、B 类位置 538，B 类作者不在身份库的 504 条（`videoUnresolved`）。
- B 类作者只按身份库已有 `current_handle` 匹配达人，OECID 提交只取 A 类 selection，工作流没有解析 B 类作者的步骤；是否补上待用户决定。
- 22:52:24 `resume-kalodata-auth`（`resume-uk-kalodata-auth-20260924`）重排 UK 轮次。用户指出 Kalodata 额度按市场分别计算：IT 耗尽后 UK 1 分钟内正常读取 49 个商品、376 条线索。UK Kalodata 正在读 A 类（到期 7,958 个商品），完成或 UK 额度耗尽后接 OECID、发送池。

## 2026-09-24 晚间：主链恢复与平台额度（用户要求）

- 17:42:52 按用户选择打开 BR、UK 自动运营总开关（revision 4）；MY 保持关闭，IT 等恢复方案。下一轮主链：BR 09-25 07:00，UK 09-26 07:00。
- 调度器随即维护到期货盘账号：IT acc9 17:45 完成。BR acc2 刷新回退为重登，17:56 `account_login_timeout`（needs_human，登录窗口无人处理）；21:49 按用户要求重登，39 秒完成，新代次 `identity-generation-b9496f8c74ef546e03e30631`，收信重连确认。
- 平台额度实测：BR 当天确认 1000 张卡后，下一张卡没有回执、两次回查都查不到（unknown）；UK 确认 1001 张后，下一张卡 20:44:45 被平台明确拒收（`it_delivery_send_rejected`）。结论：约每市场每天 1000 个新联系。两次拒收都没落账，拿不到平台原话。
- MY 18:32、IT 19:03 各 1 张卡发出后回查读不到历史（history_unavailable）而停发；20:43 原意图只读核验在历史里找到原消息，恢复发送。之后两条投递已过 30 分钟冻结期，补发文字时被许可拒绝（delivery_expired，被适配器改写成 dispatch_not_allowed）而再次停发。
- `1c7f64f`：BR/MY/UK 发送结算明确拒收（rejected），其它记 unknown；带响应的平台回执全部落账；当天第一次拒收即暂停新联系到次日；worker 遇拒收进入 waiting_capacity。20:48 对 MY、IT 执行 stop/start 以载入新代码。
- `418971a`：过期清理对“卡已确认、文字未开始”的投递只取消文字，记 partial_delivery。20:53 后 MY、IT 恢复；20:57 当天确认卡片 BR 1000、UK 1001、IT 470、MY 376，平台拒收落账 0。
- 用户决定：卡片发出后两次回查（间隔 ≥5 分钟）都查不到的，隔离该达人（转人工、不再自动发送、开人工 case），市场继续；以后同类自动处理。实现与后续修复：
  - `1045af9` 两类发送的回查接入 `quarantine_absent_card`。
  - `3f330a3` 发送 worker 遇结果未知不再退出，每 5 分钟用原请求只读回查；调度器不会重拉停在 unknown 的发送，否则 BR/UK 09-25 不会发送。
  - `7ffdd98` 并发发送名额不再计入已隔离投递：隔离后的 unknown 卡曾让 BR/UK 之后每张卡都被 `verify_before_dispatch` 拦下。
  - `c0d02d0` 建会话返回业务码 201（旧系统记为 conversation_business_rejected，会话未建成）的达人同样隔离；IT 21:25 因此停发，21:34 后恢复。
- 平台额度回执已落账：BR、UK 修复后各有一次 `it_delivery_send_rejected`，状态 3、check_code 100、`im_limit_reached`，随后停在 waiting_capacity 到次日。
- 隔离结果：BR、UK 各 1 张 unknown 卡（case `card_result_unknown`）；IT 1 位达人被拒建会话（case `conversation_business_rejected`）。
- 21:34 四市场发送进程全部换成 `c0d02d0` 代码（BR/UK/IT 旧进程用 SIGTERM 安全停止后 start）。21:36 当天确认卡片：BR 1000、UK 1001、MY 693、IT 684。
- 过程失误：`3f330a3` 合入时全量测试有 1 个失败（命令链用了 `;`）。失败的是旧冻结批次测试，“首拒即停”后变成时序竞态；`9ead7db` 改为单通道并按新规则断言。之后的合入都在全量测试通过后执行。
- 仍待修：适配器把发送许可阶段的本地拒绝都改写成 dispatch_not_allowed，调用方看不到原因码。

## 2026-09-24 恢复收信、AI 回复与二发（用户要求）

- 用户要求：先恢复收信和 AI 回复（AI 回复要看效果），再开所有市场二发；取消 500 人/24 小时上限，跑到平台机构每日额度。各市场自动运营总开关（主链）仍关闭，IT 货盘恢复另议。
- 17:16:35 用 job-run 启动 IT 收信与 IT Agent 作业，后者拉起调度器；调度器随即拉起 BR/MY/UK 的收信与 Agent worker。BR/MY/UK 收信 17:17 恢复。
- IT 收信因 acc6 维护逾期报 `live_maintenance_due`。调度器只在有市场开自动运营时做到期维护，所以 17:17 手动请求 acc6 refresh；回退为重登，17:19 发布新代次，意图 completed。IT 收信 17:22 恢复。IT acc9、BR acc2（货盘账号）仍逾期，主链恢复前再处理。
- `9d9846e` 取消本地新联系上限：同一北京日平台明确拒绝 2 次，就暂停该市场新联系到次日。系统此前从未收到过平台拒绝回执，额度用尽时平台给什么信号未知，首次触发后再按落账原生码补分类。Web build `JYe4euXh2RqoSf8XnWeGB`（上一版备份 `var/web-releases/platform-quota-20260924/previous`）。
- 17:28 对四市场二发执行 start（与暂停前一致：runRequested=true），全部失败于 `it_delivery_dispatch_not_allowed`：09-23 晚触达上限时留下 7 条已冻结、从未发出的 ready 投递（BR 4、IT 1、MY 1、UK 1），30 分钟前就已过期，发送 worker 每次先拿它们，在发送许可里撞到 delivery_expired；IT 每个调度 tick 重启一次，约 10 秒一轮只读认证。17:33:57 四市场执行 stop 止损，全程平台写入 0。
- `3d9ffbf` 让两类发送 worker 先把“过期且未发出”的投递结算为 cancelled（delivery_expired）。17:37 重新 start（revision IT 19、BR 7、MY 5、UK 7）：7 条过期投递被结算，约 2 分钟内确认卡＋文字 BR 12、MY 12、UK 9、IT 7，平台拒绝 0。
- AI 回复：四市场首发阶段为 pilot_running。今晚 00:30 起 BR/UK/IT、明早 09:00 起 MY，各先回 1 条试运行；确认后停在 pilot_complete，需要在页面开启全量。

## 2026-09-24 生产暂停（用户要求）

- 11:18–11:19 按用户要求暂停，全部使用系统自带开关，未强杀进程：
  - IT/BR/UK 自动运营总开关关闭（revision 5/3/3；MY 本来未开）。
  - 四市场持续发送执行 stop（`runRequested=false`、`stopRequested=true`，revision IT 16、BR 4、MY 2、UK 4）。
  - scheduler、IT 收信和 IT Agent 作业写入停止文件；BR/MY/UK 的收信与 Agent worker 收到 SIGTERM 后在安全点退出。
  - 11:19:45 起项目 worker 为 0，只有 Web 在运行。
- 暂停前没有在途平台写入（18 个 ready 组件未开始，另有 IT 2 个 quarantined_unknown、1 个 partial_delivery 保持原状）。Agent 开关、指南和发送模板未改。前后状态回执在本机 `outputs/pause-20260924/`。
- 暂停时未回的达人来信（与会话页未读同一口径）：BR 48、IT 29、MY 13、UK 8，另有 IT 人工会话 2 条。此前记的 IT 57、MY 15 取自收信状态的 `pendingContent`，其中包含已处理或机构后台已回复的会话。MY 收信从 10:42 起在认证阶段连续失败（`taplink_remote_read_failed`），早于暂停。
- 四个市场的 Agent V2 首发已于 09-23 16:57–17:02 在页面授权（阶段 `pilot_running`）。授权晚于当天 15:00–16:00 回复窗口，09-24 的窗口前又已暂停，所以还没有 V2 真实回复。
- 17:16–17:37 已恢复收信、AI 回复和二发，见上；主链（自动运营总开关）仍关闭。

## 2026-09-24 本机清理（用户同意）

- `var/` 中 4,458 个 `market-identity-*` 临时目录和 917 个超过 3 天的旧运行报告与日志已压缩归档到 `/Users/bjn00003/BDHub/BDHub-Agent-backups/var-archive-20260924/`，逐字节校验后从 `var/` 移除，释放 225MB；归档目录附有清单和恢复命令。仍被代码读写的 15 个旧状态或日志文件保留。
- 删除了 4 个已完全合并的旧分支，以及 31 个空目录；它们仍在离线包 `BDHub-Agent-20260924.bundle` 中。
- 按用户确认的保留策略（当前加最近 2 份）首次执行 `state-retention.py`：
  - 执行前先做了 33 库全量备份 `var/backups/state/20260924T035449Z-pre-retention-20260924`（1.57GB，verify 通过）。
  - 归档到 `../BDHub-Agent-backups/retention/20260924T035517Z`（24MB）的内容：IT 的 7 份旧货盘快照；IT 的 3 个旧全托轮次明细（127,989 行，轮次记录保留）；8 个旧采集 JSON；调度日志轮转。
  - 压缩后全托库从 172MB 降到 105MB，主库从 552MB 降到 517MB。所有库 quick_check 为 ok，各货盘 head 完整。
  - IT 两轮待恢复的来源轮次、UK 全部 3 轮都在保护名单里，未动。账号身份旧代次还在 72 小时安全窗口内，下次执行时再归档（acc4 约 1.1GB）。

## 2026-09-24 异机备份（用户同意）

- 15:51 首次写入外接 U 盘 KINGSTON：`/Volumes/KINGSTON/BDHub-Agent-offsite/20260924T075104Z/`，加密后 302MB，用时 48 秒。内容：33 库全量快照（1.47GB）、`c805ba7` 的代码离线包、16 个未提交改动的补丁、仓库外两个归档和 3 个敏感配置。盘上其它文件未动。
- 校验：写后解密回读一致；卸载重挂后从盘上重读通过。恢复演练在临时目录解开后，33 库 hash 与 quick_check 通过；代码克隆并打补丁后，与生产逐字节一致；配置与归档也一致。演练目录已删除。
- 密钥 `/Users/bjn00003/BDHub/BDHub-Agent-backups/offsite/offsite.key`（指纹 `d61ad40a9e351f63`）只在本机。用户需把内容另存到密码管理器，否则本机损坏后副本无法解开。
- 插盘自动运行（launchd StartOnMount 调 `offsite-backup.py auto --notify`）未安装，待用户确认；在此之前插盘后手动执行 `run --target /Volumes/KINGSTON --confirm`。
- 16:16 写入第二份 `20260924T081641Z`（47 秒），并首次在本机记下最新副本（`offsite/latest.json`），告警条据此判断备份是否过期。

## 2026-09-24 页面告警条（用户选定渠道）

- 用户选定只用页面告警条提醒，不发飞书或系统通知。所有市场页面顶部新增告警条，由 `/api/ops-alerts` 调 `scripts/ops-alerts.py` 只读汇总四个市场的异常，页面打开时每分钟刷新。
- 16:14 发布 Web（build `uV-5eJUz4DZGcAhSjDbKp`），上一版备份在 `var/web-releases/ops-alerts-20260924/previous`。没有启动或重启任何 worker。
- 16:20 告警条内容：
  - 生产已暂停（自 11:18）。
  - 需处理 4 项：IT 2 条人工会话；IT 货盘 `global_catalog_not_published`；UK Kalodata `kalodata_auth_required`；MY 收信 `taplink_remote_read_failed`（auth 阶段，自 10:42）。
  - 提示 5 项：IT 29、BR 48、UK 8、MY 13 位达人来信未回（最早约 22 小时）；IT 2 条未知发送已隔离。

## 2026-09-24 MY 重登与 Kalodata 激活（用户要求）

- MY：17:05 通过账号页同一接口请求 acc8 重登，项目维护 worker 用保存的凭据自动登录，本次没有出现验证。新代次 `identity-generation-7900bbb2aa1e27a3b8dcce7f` 已发布（profile 目录完整）。随后收信重连这一步因 `project_account_identity.py` 缺 `import json` 报 `NameError`，意图被记成 failed_known（这条记录保留原样）。该缺陷自 09-23 16:22（`1648c60`）起影响所有走到这一步的重登或刷新，已在 `a8e6d94` 修复并补真实 adapter 测试。
- 17:07 用新身份手动跑一轮 MY 收信（5 个会话，只读）：completed，错误清除，平台写入 0。
- Kalodata：探测确认 `auth_required` 后，17:03 用保存的激活卡启动激活，扩展验证成功，代理与 Cookie 已保存；17:04 页面上另有一次“刷新身份”（非本会话发起）。17:09 探测为 ready（50 行）。UK 的 Kalodata 阶段仍记为 needs_human，需 UK 主链重跑该阶段后才会清除。

## 2026-09-24 AI 回复窗口（用户确认）

- 用户要求：按各市场当地白天回复，且回复不能与二发冲突，只在二发之前或之后进行。二发窗口四市场都是北京时间 16:30–24:00，未改。
- 16:45 把四个市场的回复窗口从北京时间 15:00–16:00 改为下表，缓冲 30 分钟不变。直接调用 `save_agent_setting` 写入，没有经过页面 CLI（它会在 BR/MY/UK 自动拉起调度器）。Agent 开关、首发阶段和二发设置都没动。

| 市场 | 北京时间 | 当地时间 | revision |
| --- | --- | --- | --- |
| IT | 00:30–03:00 | 18:30–21:00 | 3 → 4 |
| UK | 00:30–04:00 | 17:30–21:00 | 1 → 2 |
| BR | 00:30–08:00 | 13:30–21:00 | 1 → 2 |
| MY | 09:00–16:00 | 09:00–16:00 | 1 → 2 |

- IT 当地早上只剩二发前的 09:00–10:00，与晚上无法合成一段，取较长的晚上一段。10 月 25 日欧洲结束夏令时后，IT/UK 对应的当地时间提前 1 小时。
- 16:44 发布 Web（build `5an8ErZUd2hM53vwMT3er`），上一版备份在 `var/web-releases/reply-window-20260924/previous`。作业页说明和告警条已显示新窗口。
- 恢复生产后，每个市场第一轮只回 1 条（pilot），确认后停在 pilot_complete，需要在页面开启全量；全量时每分钟最多 1 条。

## 2026-09-24 会话页修复

- 自 2026-09-23 23:14 的 Web 构建起，会话页在浏览器里读不出任何队列（`fetch` 被当作对象方法调用，浏览器报 Illegal invocation），四个市场都显示 0 条和“暂时无法读取会话队列”。台账与接口数据完整。
- 已修复并补回归测试，重新构建 Web：当前 build `jso911ocYYSg1oS4_HKVd`；上一版构建备份在 `var/web-releases/conversation-fetch-fix-20260924/previous`。

## 2026-09-24 项目审计与清理

- 回退锚点：基线标签 `audit-base-20260924`（`b4efe70`）；审计开始时生产目录未提交改动的快照 `audit-wip-20260924`；仓库外离线包 `/Users/bjn00003/BDHub/BDHub-Agent-backups/git/BDHub-Agent-20260924.bundle`。
- 分支 `codex/claude-audit-20260924` 删除了 37 个无调用脚本、6 个 lib 模块、4 个页面已不调用的 API 和 109 个过时文档，并按代码修正了主文档。活代码路径、`config/*.json`、`var/`、服务和授权都未改。清单、缺陷与建议见[项目审计与清理](../implementation/project-audit-20260924.md)。
- 被删文件都不在 scheduler、job_run 或 Web bridge 的调用路径上，合入没有重启 worker。之后因会话页修复重新构建了 Web：已删的 4 个接口现返回 404，生产目录 typecheck 通过。
- 生产目录原有的 16 个未提交改动（与 IT workflow 恢复相关）保持原样，如何处置仍待用户决定。

## 本轮交付与验证

- 上轮已交付：四市场主动推品冷却 72h；机构后台外发正文进入会话与 AI 上下文；有真实卡的肯定合作场景补加橱窗提醒。MY 6 会话 7 条机构正文经本地与 HTTP 时间线核对，缺失/重复 0。
- 新增 `inbox-history.py`：原生 OLDER 分页、身份绑定、页级回执与断点。补扫不吞新达人回复、不创建 pending、不改关系/授权、不解除 gap 或 unknown；机构外发超过热读 20 条窗口仍可补正文。
- MY 两会话分别读完 3 页 13 条、3 页 6 条，第一会话跨进程续读；返回消息都已存在，新增 0、延后 0。证明本次分页、断点和幂等，不能外推全市场历史完整。实时窗口外的延后达人消息仍需独立恢复策略。
- V2 四市场 × 12 多轮合成评测已加入仓库。最后一轮 47/48 筛查通过；MY 无卡场景主动澄清且 reply/clarification 冲突被生产校验拒绝。另有三条内部中文解释精度观察；原始输入输出和失败样本保留，不作为生产准确率。
- 新增只读备份保留计划与发送耗时诊断。31 份完整备份校验通过；最新 33 库约 1.48 GB 在空目录恢复并验证，用时 5.043 秒，临时副本已清理。保留策略删除候选 0，未删除备份；异机副本尚未完成。
- 最终 Python 全量 1,250 项、24 项相关 Web 测试、文档链接与 diff 检查通过，无 ResourceWarning。本轮没有 Web 源码变更，不重复构建或更换在用 Web。

## 运行与数据回读

- `second-cycle` 已备份后应用 v19/v20 历史覆盖迁移。v20 前备份 `var/backups/state/second-cycle-before-history-v20-guide2-20260923T161345Z.sqlite`，527,781,888 字节，SHA-256 `cb41ad40bc93a4367d8f7d470a7fd3aa2c5c5264bbf3f35465a823889ac1cf2c`，quick_check=ok。所有 registry 检查通过。
- 应用户空间清理请求，先新建并验证 33 库完整备份 `var/backups/state/20260923T164039Z-pre-storage-cleanup`，再按明确清单删去 20 份中间重复快照，实测释放 11.49 GiB；12 份保留的多库备份逐份通过 hash 与 SQLite 校验。单库事故备份、其他 6 组旧存档、当前数据库和服务未动。明细回执在本机 `outputs/backup-cleanup-20260924/`；这仍是本机备份，异机副本尚未完成。
- 四市场指南已保存为 revision 2，hash `9a2f2285dfe191d050321a2ec9ab3bbc919d2bd50534722f6b6addffe6e7da8f`，HTTP 回读一致。明确输出格式、纯感谢、重复提醒、无真实卡和无依据时限承诺的处理。
- 核对原 PID、锁、窗口和无在途回复后，仅重载既有 Agent worker：IT 35998、BR 36004、MY 36010、UK 36017；均 outside_reply_window。原 control_event、Agent 设置、发送控制、delivery_part、service_reply、pending 逐表 hash 未变。
- Web 保持 build `sG39QqsWI2i7rQLD5ti1Z`、5198 PID 25908；scheduler PID 15244 与发送/收信 worker 本轮未重启。发送组件 started 仍 4,999，本轮新增平台写入/真实发送 0。
- 本轮平台调用仅原账号只读历史与选入映射核验；模型调用仅合成评测。真实运行操作由主 Agent 单独执行。回执在 `outputs/audit-next-20260923/`，评测在 `outputs/agent-v2-eval-20260924-followup-v3-reviewed/`，备份/吞吐报告在 `outputs/audit-20260923/`。

## 未完成业务与接续

- IT 原轮次 `workflow-9285ce7cb0c7427f9e13c0a67389` 已按用户选择续跑（见上），重复轮次 `workflow-776f77d39bc2982e537f82670933` 已停止。09-23 20:30 的 campaign 轮次 `workflow-038da43ec820264f5b155e183fdd` 仍为 needs_human/kalodata_video_author_missing，其视频代次已被新一代取代，未续跑。
- 两 PID 尾号 77284、73153 已在 ACC9 已选池，但当前子活动与冻结活动不同，映射证据不全；按用户选择改为 `isolated_unverified`，证据保留，本轮不建链接。活动映射核实后是否恢复另议。
- 在用 scheduler（09-24 17:16 启动）已加载同轮 source 复用的未提交改动，本轮货盘阶段按该路径复用已发布 source。历史 platform_writes 累计保留，当前状态优先显示 active run。
- 原有 catalog/global/identity/scheduler 改动保留；本轮仅接纳 workflow 的累计写计数与 active 显示修正，其余不夹带提交或借发布自动执行。
- IT 两条建会话 quarantined_unknown 与商品选入映射是不同问题；均保留原意图，不能因 72h 到期重发。每市场 30 位/分钟仍未达成；继续先补分段耗时，再评估提速。批量历史补扫、真实多轮人工标注是下一阶段方向（异机备份已于 09-24 完成）。
