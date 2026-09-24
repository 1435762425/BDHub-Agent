# 项目审计与清理（2026-09-24）

范围：全仓代码、Web、文档与本机运行形态的只读审计，以及在分支 `codex/claude-audit-20260924` 上完成的删除和文档修正。本文同时承接 2026-09-23《审计后整改方向》中仍有效的建议（原文已并入本文并删除）。数字均注明时间；本文不授权任何平台动作，当前运行状态见[交接](../handoff/current.md)。

## 1. 结论

1. 版本控制已兜底：基线标签、未提交改动快照和仓库外离线包都已建立；合并前生产目录没有任何改动（§2）。
2. 删除了已证实无人调用的内容：37 个脚本、6 个 lib 模块、11 个 Python 测试文件（另删 2 个用例）、4 个 Web API 及其桥接和测试、4 个图标、109 个过时或孤立的文档文件，合计约 1.5 万行（整个分支 +314/−15,173）。活代码路径没有改动（§3）。
3. 主文档与代码有 22 处不一致：TECHNICAL 已按代码修正；PROJECT 只加“实现范围/缺口”标注；AGENTS.md 补上“本目录即生产”等事实（§5）。
4. 发现 1 个需要排期的缺陷：新一代 B 类线索一开始就清空当前 B 类位置（§6）。
5. 最大的业务短板在“回复→转化”，不在发送速度：已确认触达 2,499 位达人、134 位回复，但 V2 Agent 尚未真实启用（§7）。
6. 剩余技术债集中在“名字过时但仍在用”的模块，以及 IT 与其他市场两套发送路径，需要先切断调用再删（§4、§8）。

## 2. 版本控制与回退

| 项 | 内容 |
| --- | --- |
| `audit-base-20260924` | 审计前 HEAD（`b4efe70`） |
| `audit-wip-20260924` | 审计开始时生产目录 16 个未提交改动加 `CLAUDE.md` 的快照，只用于回退，不用于合并 |
| 离线包 | `/Users/bjn00003/BDHub/BDHub-Agent-backups/git/BDHub-Agent-20260924.bundle`，含全部 ref，已试恢复并 fsck；不含被忽略的密钥配置和 `var/` |
| 清理分支 | `codex/claude-audit-20260924`，在 `/Users/bjn00003/BDHub/BDHub-Agent-audit` worktree 中完成 |

- 未合并时：删除 worktree 和分支即可。
- 合并后：用 `git reset --keep audit-base-20260924`，或按提交 `git revert`。不要用 `reset --hard`，它会冲掉生产目录的未提交改动。
- 取回任一已删文件：`git show audit-base-20260924:<路径>`。

## 3. 本轮删除清单

判定规则：删除后全仓再无任何文件引用。引用包括两类：AST 导入图（含函数内延迟导入）；字面路径引用（spawn argv、`spec_from_file_location`、`runpy`、Web bridge、scheduler/job_run 的 argv）。历史报告里的纯文本提及不算引用。

| 类别 | 内容 |
| --- | --- |
| 批量/V1 发送链 | send-batch、bulk-second-send、batch-preparation-worker、batch-task-control、inspect-batch-preparation、cycle-send-cohort、run-second-cycle、advance-cycle-materials、prepare-cycle-review、run-auto-replies、evaluate-cycle-replies、query-cycle-reply-facts、benchmark-identity-lanes/limits；lib：batch_material_runtime、batch_preparation、cycle_agent |
| 意大利试点与调研探针 | export-italy-profile-completion/signals/second-readiness、probe-italy-cards、probe-italy-second-profiles、audit-italy-account-pair、catalog-link-canary、create-cycle-card、probe-mx-im-context |
| 一次性验证、维护与模拟 | validate-identity-release、validate-catalog-read-routing、rebuild-current-leads、restore-kalodata-cache、material-maintenance、kalodata-video-evidence、enrich-global-opportunity、simulate-lead-priority、matching-budget.mjs、resume-global-category（固定传 IT `--by-category`，采集脚本已拒绝）、check-vendored-runtime（已被 `vendor-legacy-bdhub.py --check` 取代）；lib：lead_priority（与实际排序 `lead_pool.strength()` 重复的第二份实现）、lead_rebuild、material_maintenance |
| Web | `/api/catalog-screen`、`/api/global-source`、`/api/cycle-service`、`/api/reply-review` 及其 bridge、`reply-review-contracts.ts`、5 个测试；只为它们服务的 global-screen.py、cycle-service.py、reply-review.py；4 个未被引用的 SVG |
| 测试 | test_auto_reply_runner、test_legacy_bulk_retired、test_batch_material_runtime、test_batch_preparation、test_cycle_agent、test_italy_cards_probe、test_profile_completion_export、test_profile_second_batch、test_lead_priority、test_lead_rebuild、test_resume_global_category；test_batch_materials 删 2 个用例 |
| 文档 | 109 个文件：batch 时代和 second-cycle v1 的设计与实施、意大利试点和 matching 报告、DeepSeek/UI/TailAdmin 调研、早期 PRD/决策/状态日志、18 张 design/qa 截图和 template-catalog.html、排序模拟报告（其代码已删）、上一版整改方向（已并入本文） |

保留的无调用工具，各有明确用途：
- 新市场接入：onboard-markets、market-send-canary、probe-market-im、audit-market-account-pair。
- 迁移回填：backfill-* 共 3 个。
- 覆盖恢复与运维：reconcile-global-coverage、global-source-control。
- TikTok 合同工具链：audit-tiktok-*、render-tiktok-interface-docs、probe-tiktok-field-shapes。
- Web 性能：web-performance-trace.mjs。

## 4. 保留但属于技术债（需先切断调用）

| 项 | 为什么没删 | 处理方式 |
| --- | --- | --- |
| `cycle_conversations.py` 与 index-italy-conversations.py | 这是在用 IT 会话索引库的唯一建表定义 | DDL 移进迁移注册表后再删 |
| `cycle_card_creation.py` | 生产目录未提交的 `tests/test_catalog_prepare.py` 仍引用它 | WIP 处置后清理 |
| `send_batch.py` 第 178–674 行，及它牵住的 cycle_burst、cycle-send、send-batch-worker、test_cycle_burst | 活代码只用 `window_state/capacity/NEW_CONTACT_LIMIT/load_config` | 抽出发送窗口/额度模块后整体删除 |
| `reply_events.py` 第 145–612 行（V1 分类/评测），及 `typesafe_provider.py`、`config/typesafe*.json` | 活代码只用 `backfill`、`load_policy` | 拆分后删除 V1 部分 |
| batch_* 家族（tasks、task_service、sources、materials、source_runtime） | 活代码只用 `batch_source_runtime.kalodata_provider`；`batch-tasks.sqlite` 仍存着身份运行策略 | 迁出 Kalodata provider 和身份策略 |
| identity_soak/stress，以及 identity_acceptance 的大部分 | 被 `probe-italy-profile.py` 延迟导入 | 切断压测分支后删除 |
| `second_cycle.py` 的旧供应队列（claim/page/replenish/status）、cycle_speed、cycle_scheduler | 只剩旧 worker 和测试在调用 | 随 second-cycle-worker 一起清理 |
| `second_live_runtime` 的旧系统门禁 | IT 每次写入前都要查旧系统 PostgreSQL 和写门禁目录 | 需业务确认旧发送器永久退役后再移除；否则旧库一停，IT 发送和回复就会失败 |
| 名不副实的在用模块 | `market_send_canary`（三市场正式发送核心）、`second_cycle`（四市场核心存储）、`italy_im_*`（四市场共用）、`probe-italy-profile.py`（各市场身份查询） | 单独提交改名，同时更新 scheduler 的 argv |
| 活文件中的 Web 死代码 | 8 个 UI 从不发送的写动作、未用的 export/import、约一半未用的 hook 返回值、14 个未用的 CSS utility | 随重构清理，需要页面回归 |

## 5. 文档与代码不一致（本轮已修正）

- **TECHNICAL**，改正的内容包括：
  - 排序在 `lead_pool.strength()` 实现；`request_budget` 只是进程内节拍。
  - 补上 IT 专属能力清单；非 IT 市场不做 TapLink 清理；CLI 的 `--market` 缺省为 it。
  - `/api/creator-identities` 直读 SQLite；`batch-tasks.sqlite` 仍在用。
  - DeepSeek provider 及其旧项目 key 回退；IT 写入前的旧库检查；BR/MY/UK 的身份链路。
  - `jobs.json` 各开关的实际语义；页面会改写 `config/*.json`。
  - 补齐漏列的 7 个 API、5 个配置文件和市场接入 canary 库；注明 9 个已无代码读写的旧库仍在备份清单里。
  - 写明本目录即生产，开发走 worktree。
- **PROJECT**：规则不变。
  - B 类线索、非 IT 的 TapLink 清理加“实现缺口”标注。
  - 成功判据改为“平台受理，并能按原请求回查到 messageId”（原 `flight` 码属已退役的浏览器通道）。
  - 写明已解锁关系不占新联系名额。
- **AGENTS**：
  - 旧项目是运行时只读依赖；本目录即生产。
  - `config/*.json` 会被页面改写。
  - bridge 规则有已知例外；Web 先 build 后 typecheck。
- **其余**：
  - README 和 Web README 的运行方式与路由表。
  - vendor README：MX 探针已删。
  - 第三方许可说明：ApexCharts 和示例图已移除。
  - TailAdmin 导入清单：去掉 50 个已不存在的文件。
  - 5 份被链接的设计文档修正了过时的横幅或规则。
  - 交接文档改名为 `docs/handoff/current.md`。

## 6. 缺陷与实现缺口（未改代码，需排期或决定）

| 优先级 | 问题 | 证据 | 建议 |
| --- | --- | --- | --- |
| P1 | 新一代 B 类初始化时立即清空 `video_lead_current` 和 `kalodata_video_head`，扫描完成前 IT 的 B 类位置消失 | `kalodata_video_scan.py` 初始化分支 | 改为新一代完整后再原子切换 head |
| P1 | IT 发送和回复依赖旧系统 PostgreSQL 在线 | `second_live_runtime.conflicts()` | 决定是否保留旧系统门禁；保留就纳入监控 |
| P2 | B 类只在 IT 运行，也没有 7 天重读节奏 | `operations_scheduler.py` 的 Kalodata 阶段 | 决定 BR/MY/UK 是否上 B 类；按 PROJECT 的节奏实现，或修改规则 |
| P2 | BR/MY/UK 没有 TapLink 清理，也没有本地停用 | scheduler 的 `taplink_clean` 分支 | 至少实现本地停用和只读扫描 |
| P2 | 每次后台运行都打开日志文件且不关闭，常驻的 Next 进程会泄漏句柄 | `server/leads-queue/bridge.ts`、`server/catalog-names/bridge.ts` | 子进程启动后关闭父进程持有的句柄 |
| P2 | campaign-join 把所有以 `campaign_` 开头的 CLI 崩溃都映射为 409，并把 stderr 尾部回传前端 | `app/api/campaign-join/route.ts` | 区分业务错误码与崩溃，只回稳定码 |
| P3 | 两个 Web 测试依赖本机环境（一个真实读取 `var/`，一个要求目录名） | `campaign.test.mjs`、`creator-profile-refresh.test.mjs` | 改用临时根目录 |
| P3 | scheduler 有死分支：`if True:`、未用的变量、只被测试调用的函数 | `operations_scheduler.py` | 拆分 scheduler 时一并清理 |

## 7. 业务漏斗与流程建议

2026-09-24 10:48 的只读快照（`second-cycle.sqlite`，mode=ro）：

| 市场 | 已确认触达 | 回复达人 | 橱窗证据（collaborated） |
| --- | --- | --- | --- |
| IT | 993（另有 2 个 unknown 被隔离、1 个部分送达） | 65 | 75 |
| BR | 501 | 46 | 22 |
| MY | 500 | 15 | 4 |
| UK | 505 | 8 | 10 |
| 合计 | 2,499 | 134 | 111 |

Agent V2 只运行过 70 次模拟，台账里没有 `agent-v2-first-send` 授权事件。累计真实服务回复 10 条：8 条样品自助，2 条转人工确认。

建议（按业务影响排序）：
1. **先解决“回复→转化”**：
   - 由用户在页面给 V2 做首发授权，并观察首条回执。
   - 四个市场共用北京时间 15:00–16:00 这一个回复窗口（巴西当地是凌晨），而账号在 00:00–15:00 空闲。建议按各市场当地营业时段分别设回复窗口。
2. **重新评估 30 人/分钟的目标**：各市场都已顶到 500/24h 门禁。按现在约 3–9 人/分钟的速度，也能在发送窗口内发满，提速不会增加日触达量。应先决定是否调整上限；不调的话，把精力放在线索质量和回复处理上。
3. **建立 cohort 追踪**：按市场、A/B、模板、佣金差，串起“完整触达→真实回复→橱窗→可取得的成交”。分母、观察期和缺失率都要写清楚；橱窗率不等于销量。
4. **明确各市场的能力**：把 IT 专属能力写进 `markets.json` 的能力位，页面按能力位显示，而不是靠代码硬编码。

## 8. 工程与运行建议

- **结构**：
  - 按原计划拆 `operations_scheduler.py`（到期计算、阶段执行、报告解码、进程监督），`operations_workflow.py` 仍是唯一状态机。
  - 拆 `reply_events`（事件投影 vs V1 评测）和 `send_batch`（窗口额度 vs 旧批次）。
  - IT 与 BR/MY/UK 共享不可变意图、资格、组件结算和 reconcile 合同，各市场只保留 transport 适配。目前重复的点有：控制读写、容量、候选选择、写前复检、会话预检、回执核验、worker 启动、认证。
- **数据**：迁移注册表只覆盖 2 个库，另有 34 个文件共 128 处建表语句；33 处 `executescript` 会隐式提交事务。应逐步收进迁移注册表，保证旧库升级幂等且有测试。
- **代码风格**：49 个 Python 文件和大量 TS 是一行多语句或超长行。在 worker 空闲时用格式化工具做一次纯机械提交，并加 lint（ruff/eslint）。164 处 `except Exception` 需要逐步收窄。
- **Web**：抽出共享的 Python 调用、请求校验、JSON body、错误格式和轮询 hook；本机校验的端口改为可配置，让开发实例能与生产并存。
- **运行**：
  - 生产改为从固定 tag 的发布目录运行，开发只在 worktree 进行。
  - scheduler 和 worker 用 launchd 常驻。目前重启电脑后它们不会自动恢复；Web 是非持久的 `launchctl submit`。
  - 给 `var/` 建立保留策略：4,458 个 `market-identity-*` 临时目录、944 个超过 3 天的 json/log、失效的 lock/pid。
  - 9 个旧库做一次终备份后移出轮转备份清单。
  - 补上异机备份：私有 git remote 加状态库副本。
  - 消除写死的 `/Users/bjn00003/...` 路径。
  - 厘清旧项目 `io.bdhub.account-identity-lifecycle`（每 300 秒运行）与本项目 72 小时账号维护的分工。
- **协作**：
  - 交接文档过去两周被提交 89 次；运行快照应改为低频提交，或放在 `var/`。
  - 建立 `main` 主干和短命分支。
  - 加 pre-commit：check-docs、孤立文档检查、快速单测。
  - 同一时间只让一个 Agent 改仓库。

## 9. 承接 2026-09-23 整改方向（仍有效）

| 优先级 | 问题 | 可核验的完成指标 |
| --- | --- | --- |
| P1 | 双向消息完整性：在实时热点和历史补扫之间分配预算，记录各市场的发现/内容水位与 gap | 对选定时间范围按 messageId 对账：缺失 0、重复 0、跨市场 0；报告实时延迟 P50/P95/最大值和旧队列剩余数 |
| P1 | unknown 只按原意图核验，并保存未知年龄、最后核验时间和证据缺口 | 停止或窗口外的核验不调用新发送；重复核验不新增意图；回执失配时保持未知 |
| P1 | 阶段结果统一记录完成范围、stop reason、原 generation、写入数和 unknown | 故障注入覆盖锁占用、空报告、partial、quota、崩溃、迟到 fence；未完成的输入不发布完整 generation |
| P1 | V2 真实多轮评测，覆盖六类：肯定合作、纯感谢、已加橱窗、已提醒、无真实卡、机构已人工回复 | 每类覆盖四个市场；提醒的触发与不触发分别统计；转人工漏判、虚构承诺、重复回复逐条审核 |
| P2 | 发送耗时拆分：建会话、认证/锁、卡提交/回查、文字提交/回查、本地取人 | 各市场连续分钟内的确认数、P95、错误数、unknown 和等待占比；以真实卡文双确认为准 |
| P2 | 迁移注册表覆盖全部库，用增量对账代替每轮全库回填 | 旧库副本升级幂等；投影重建前后数量、身份、回执不变；全程无平台调用 |
| P2 | 异机备份与恢复演练 | 在空目录恢复到可只读启动，schema/hash/数量一致；记录恢复耗时和丢失窗口 |
| P3 | 经营结果 cohort（见 §7） | 各层的分母、观察期、缺失率明确；未取得成交证据时保持未知 |

上一轮已完成的事项不再列入：72 小时统一冷却、机构后台外发进入会话、加橱窗提醒、历史补扫原生分页、备份保留建议与发送诊断。

历史实测只证明对应样本：
- 四市场启动时，容量未满的情况下 IT 约 5–7、BR/MY 约 7–9、UK 约 4–6 人/分钟。
- OECID 的 82 位 cohort 约 30.6 位/分钟。
- V1 人工评测 35 条中，DeepSeek 正确 28 条。
- 零销量视频证据来自 5 个 PID 的样本。

详见[四市场启动报告](four-market-launch-implementation-20260923.md)、[OECID 吞吐](identity-oecid-throughput-20260920.md)、[V1 评测](reply-model-evaluation-20260920.md)、[视频证据](kalodata-zero-sale-video-evidence-20260920.md)。

## 10. 本轮验证

以下均在 worktree 中执行；生产目录与服务未改动。

| 检查 | 改动前 | 改动后 |
| --- | --- | --- |
| Python unittest | 1,242 通过 | 1,202 通过；减少的 40 个全部来自被删的测试文件和 2 个用例，逐 ID 比对，无新增失败 |
| Web `npm test` | 182 项，180 通过 | 163 项，161 通过；减少的 19 项来自被删测试；2 项失败是既有的环境依赖（§6） |
| build / typecheck | 通过 | 通过（23 个 API 路由） |
| check-docs | 2 处链接指向本机 `var/`（所在文档已删） | 通过 |
| vendor 校验 | 169 个文件通过 | 通过 |
| 分支加未提交 WIP 的组合测试 | — | WIP 在分支上干净应用，Python 1,210 个全部通过（含 WIP 自带的 8 个） |
| 运行态引用核对（只读） | — | 生产 `var/` 的作业状态文件和 workflow 台账对被删脚本的引用为 0 |

合并后第一次在生产目录跑 `npm run typecheck`，会因旧的 `.next/types` 仍引用已删的 4 个路由而报 TS2307；重新构建后消失。重新构建之前，在用的 `.next` 仍保留这 4 个路由；页面不调用它们，若被直接请求，会因后端脚本已删而返回错误，不影响其他页面。
