# 四市场只读运营概览：第一批交付（2026-09-25）

范围：落实模块优化方案的第一批“只读运营面板与统计口径”。代码在独立 worktree `/Users/bjn00003/BDHub/BDHub-Agent-ops-overview-20260925` 开发，分支 `codex/readonly-operations-overview`；代码提交 `7b812bf`（测试隔离）与 `86e31b4`（面板）。已快进合入生产工作区代码，合入前后原有未提交方案文档逐字节保持不变，没有代为提交这些文档。

## 交付内容

- 新增只读 `market-overview.py --market <market>`、`lib/market_overview.py`、`GET /api/market-overview` 和首页经营概览。沿固定 argv bridge，显式市场、本机来源校验、稳定错误码、同市场 singleflight 与 30 秒进程内缓存；浏览器按分钟刷新，隐藏时不轮询。
- 当前货盘 PID、active 链接绑定、可发达人、未回会话分单位展示。累计合格候选台账尚未实现，显示不可用，不把当前货盘当历史累计。
- A 类身份分别统计当前来源行与去重 handle，按已解析、已判定搜索不到、排队/处理中、技术阻断、未有解析记录和绑定/状态待核对划分，两个计数分区均有恒等式。
- 查询 scope 取当前 A head，按同市场历史解析绑定复用 handle；discovery 判断通过 batch.market 隔离。无记录不声称一定从未发过请求，读取失败不报 0。B 类沿实际当前投影，只在有可用市场数据时展示；旧无 market 表不暴露给其他市场。
- 近 7 个北京日复用 cycle_stats，卡文完整确认的达人统计与单卡确认区分；回复/橱窗计事件且排除历史补录，7 日触达合计为每日去重后的“人次”，服务回复包含人工与自动。没有历史库存快照，不补造历史漏斗。
- 页面分“已处理 / 正在等待 / 需要人工”；投递未知、技术隔离和历史技术人工案件另列。旧人工锁/case 不因为只读分类而解除；等待读取的是持久状态，标明记录时间，不声称已核实 worker 存活。
- lead_pool 原 counts.queued 保持原发送候选排队语义，新增 candidateQueued 明确别名；原来被同名字典键覆盖的身份排队数据改为 identityQueued，不改变资格或发送顺序。
- 原 Campaign bridge 的输出解码抽成纯函数，保留原传输行为；两处依赖生产台账/目录名的 Web 测试改成独立数据与目录标记验证，测试不需要连接生产 var。

## 验证

- Python 全量 unittest：1,305 项通过。
- Web Node 测试：174 项通过。
- Web production build 与其后 typecheck 通过；构建只在 worktree 执行。
- 新增针对四市场、缺库/坏库/缺表、身份分区恒等式、跨市场同 handle、历史终态复用、B 旧表隔离、链接/货盘去重、历史技术 case 保留及只读哈希不变的测试。
- 日期边界、历史补录、卡确认但文字取消等复用既有 cycle_stats 测试；bridge 检查跨市场、非零写入、数量不一致、非法请求及缓存隔离。
- 四市场真实本地台账只读核对：所有市场供给、身份、发送池、处理状态、来信及日统计可读；非 IT B 类显示不可用，与实现范围一致。单市场汇总约 0.6–1.8 秒，为本次快照测量，不是长期性能保证。
- 当时 IT 搜索不到 1,406 条当前 A 来源行，对应 728 个去重 handle；BR 为 73/43，MY 为 541/146，UK 为 1,104/882。仅证明该次读到的分区口径，不替代后续实时数字。
- 使用独立 5209 预览进程与拦截 API 的只读快照检查 1440px 桌面和 390px 手机显示，均无页面级横向溢出；没有调用任何控制 API。预览截图在 worktree 的 outputs/operations-overview-preview 中，不入 Git。

## 运行与边界

生产代码已合入，**2026-09-25 14:47（北京时间）经用户明确允许动进程后完成 Web 发布**。只重启 `io.bdhub.agent.web`，未停止、重启或恢复任何生产业务 worker。新页面在原 `127.0.0.1:5198` 提供服务。

没有数据库迁移、业务状态写入、配置/凭据修改、平台请求或模型调用；读取 SQLite 使用 mode=ro，并未将 worktree 连接为生产 var。发布写入 Web 构建与本机发布回执，原服务日志按既有配置继续写入；不写业务台账。临时预览由本任务启动并在显示验证后关闭。

本批没有提前实现 A50、B 四市场扩围、15 天全托探查、80/20 发送或任何新自动隔离政策；页面如实显示当前台账，并不伪装后续方案已上线。旧只读状态读取的多个数据库不是跨库原子快照，页面展示汇总时刻、来源与个别来源更新时间；缓存只存在 Web 进程内，不另写持久状态源。

## 发布验证与回退

- 复用 worktree 已验证构建，准备副本逐文件 SHA-256 与原构建一致后切换，未在生产目录重新 build。启动命令、cwd、端口、日志位置及持久业务控制不变。
- 新 Build ID：`LJr-fYqji0wCeI4Cd_vFb`；旧 Build ID：`JYe4euXh2RqoSf8XnWeGB`。Web 监听 PID 从 `41141` 切换为 `29000`。
- 四市场 `/api/market-overview`、`/api/operations-home`、市场页面均 HTTP 200；新接口 readOnly=true、platformWrites=0、realSends=0，身份分区恒等式和 7 日数据通过；非 IT B 不可用与当前代码范围一致。
- 真实生产页面在四市场均完成浏览器渲染，无 pageerror 或页面横向溢出；IT 390px 窄屏也通过。检查只访问 GET，没有点击任何业务控制按钮。
- 切换前后记录的 13 个 scheduler/收信/Agent/二发 worker PID 均保持一致。`continuous_send_control`、`agent_reply_setting` 与 `control_event` 的只读摘要哈希前后一致；没有执行任何控制写请求。
- 旧构建备份与发布证据：`var/web-releases/readonly-overview-20260925T064754Z/previous`、同目录 `release.json`。回退时保留当前构建，恢复 previous 为 `apps/web/.next` 后只重新拉起 Web，再回读；不触及业务数据库或 worker。
- 生产截图保存在开发 worktree 的 `outputs/operations-overview-preview/production-desktop.png` 与 `production-mobile.png`，不入 Git。发布临时脚本已清理，旧构建继续保留用于回退。
