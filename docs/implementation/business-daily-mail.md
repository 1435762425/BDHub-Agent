# 每日经营日报

用户确认的经营日报：北京时间统计，按除英国、意大利之外的运营市场逐行展示，最后汇总；未接入市场列状态，不计为业务 0。包含可用商品（全托／Campaign）、有效 TapLink 准备率、Kalodata 当日新增 A/B 线索、发送消耗及剩余池、当日发送、累计回复和橱窗、未回及人工事项。不包含 GMV、内容发布、机构佣金、财务、7 日转化或批次分析。

## 当前投递：Codex 对话

用户已取消邮件通道，改为每天北京时间 09:00 在当前 Codex 对话直接接收日报。使用对话内心跳，沿用当前上下文；每次运行 `.venv/bin/python scripts/business-daily.py preview --output outputs/business-daily-codex/latest`，读取输出的 `report.json` 和 `email.txt`，在对话中完整展示分市场 Markdown 表格及末尾汇总，不只给文件链接。

日报事件默认取上一北京自然日，累计截至该日末；库存、待办和货盘来源标明实际采样／更新时间。每次重新读取真实台账，不用旧稿或模拟数据代替当前结果。任务只读台账，允许写本次导出，不改代码、业务库或运营控制，也不启动、重启 TikTok 任务。

这是读取本机项目的定时任务，需要电脑和 Codex 应用保持运行。邮件私有配置已设 `enabled=false`；邮件 LaunchAgent 已卸载并移除开机入口。保留此前邮件实现用于历史追溯，不自动恢复邮件，不操作 Gmail。

## 数据与口径

- 商品使用 `catalog_head` 指向的完整发布货盘，按现有 `assess_offer` 资格复核；每市场 PID 取当前最优方案，来源不重复计。TapLink 使用 `catalog_current_binding`，要求精确来源／PID／Campaign、方案 fingerprint、有效状态以及当前命名、分佣版本；不创建或远程刷新链接。
- 新线索求和 `lead_publication_contribution.new_pairs`，按 `published_at` 的北京日及 A/B 分类；不是抓取行数或已有线索刷新次数。首次记录之前的贡献不能回补成已知增量。
- 剩余池复用 `lead_pool.pool`：可发送＋等待；暂不参与另列。发送消耗为当日确认卡的达人×PID 去重数，不包含没有退出事件台账的失效数量，因此不承诺“昨日剩余＋今日发现－消耗＝今日剩余”。
- 当日发送达人只计卡文双确认；确认商品卡另计，结果未知／技术隔离保持原状态。累计发卡按 OECID×listId 去重，累计发送达人按市场内 OECID 去重。累计回复只计已完整发送达人在确认发卡后同会话的新回复。
- **当前橱窗通知不含 PID/listId**，不能重建逐卡采用证明。统计同达人、同会话确认发卡后、非历史且时间已知的新增通知；“累计通知／累计商品卡”明确是通知观察比例，不是精确匹配商品卡的采用转化。将来收到原卡标识后才可增加精确口径，不能按时间邻近补造。
- 来信库存复用会话页的 `unread_items`，保留最早未回答问题的等待时间。已回复人数只计实际确认的 AI／人工回复；无需回复不伪装成已发回复。技术案件与商业人工事项分列。
- 比例按合计分子／合计分母，缺数据不补 0，分母为 0 时不宣称 0% 转化。累计截至报告日末；库存和当前待办标明实际采样时间，来源更新时间保留。已经保存的每日报告不覆盖。

## 历史邮件配置与运行（已停用）

私有配置位于 `private-data/business-daily-mail.json`，模板为 `config/business-daily-mail.example.json`。`recipients` 为收件人；`sender`、`smtp.host`、`smtp.port`、`smtp.security`、`smtp.username` 配置发件通道。仅支持 SSL 或 STARTTLS。密码单独保存在配置指定的 `private-data/` 文件，不入 Git、不出现在命令 argv 或报告里。暂停邮件可将 `enabled` 设为 false；不影响 TikTok 控制。

Gmail 使用 `smtp.gmail.com:465`、SSL、完整邮箱地址作为 username，以及用户生成的 16 位应用专用密码；Google 显示的分组空格读取时自动移除。TLS 使用项目已锁定的 `certifi` CA bundle，并保持服务端证书和主机名验证，兼容本机 Python 默认 CA 路径为空的环境。

独立 LaunchAgent `io.bdhub.agent.business-daily` 每分钟调用 `scripts/business-daily.py tick`。新北京日第一次执行时冻结前日事件和实际采样库存，到 `sendTime`（默认 09:00）后发送；休眠／关机错过时醒来补生成，并明确实际采样时间，不伪称取得日末库存。缺发件配置时生成报告并等待，不提交邮件。

报告位于 `var/business-daily/reports/YYYY-MM-DD/`，包含 `report.json`、`email.txt`、`email.html`。发件状态位于 `var/business-daily/delivery/YYYY-MM-DD.json`；全局 runner 文件锁防止重入。

```sh
.venv/bin/python scripts/business-daily.py preview --date YYYY-MM-DD --output outputs/daily-preview
.venv/bin/python scripts/business-daily.py capture --date YYYY-MM-DD
.venv/bin/python scripts/business-daily.py tick
.venv/bin/python scripts/business-daily.py install-schedule
```

`preview` 只读业务台账并可导出到指定目录；`capture` 只写派生报告；只有 `tick` 在已配置且达到发送时间时提交邮件。没有 TikTok 写入，也不读取原始 BDHub 项目。

`install-schedule` 把 plist 保存在项目 `var/business-daily/`，在用户 LaunchAgents 中添加指向本项目的入口并注册；已有同名非本项目任务不会被覆盖。用户登录后会自动加载，睡眠时不保证准点运行，醒来由下一次 tick 补执行。

## 历史邮件交付合同

提交前冻结 Message-ID 并将当日状态记为 `submitting`；已成功、提交中或结果未知不会重复提交。连接／认证阶段明确未提交，最多 3 次、间隔至少 15 分钟；DATA 提交期间断线记 unknown，不盲重发。SMTP 接受仅表示服务商收件，不证明收件箱最终到账。进程被中断且停在 submitting 同样等待原 Message-ID 核验，不另起发送意图。

本任务不改动现有运营开关或业务 scheduler，不需要重启现有发送、收信、回复进程。生产保留的 `config/jobs.json` 运营改动不属于此功能。
