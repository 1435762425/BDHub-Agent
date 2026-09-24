# BDHub-Agent 当前交接

更新时间：2026-09-24 22:58（Asia/Shanghai）。本页只记录运行快照；规则见 [PROJECT](../PROJECT.md)，实现见 [TECHNICAL](../TECHNICAL.md)，后续方向见[项目审计与清理](../implementation/project-audit-20260924.md)。更早流水见[历史交接](../archive/handoff/codex-takeover-history-20260923.md)。

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
