# 旧项目资料与自定义 Skills 清理登记

核对日期：2026-09-11。状态：只读调查完成，**所有清理动作尚未授权执行**。本文件仅写入新独立项目；未删除、移动、改写旧 BDHub、全局配置或 Skills，也未卸载插件、修改记忆、运行平台业务动作或改变服务。

新项目为 `/Users/bjn00003/BDHub/BDHub-Agent`。旧项目 `/Users/bjn00003/BDHub/01-BDSystem-V2` 保持只读；此登记不授权后续代理自动清理。文件旧、体积大或未被文本搜索命中，都不能单独证明无用。

## 1. 盘点范围与证据限制

本轮通过文件元数据、目录分类、实际正文、构建脚本依赖、当前入口引用和有限哈希比较判定。没有读取业务消息正文或重新统计生产数据，也未验证所有输出能从今天的数据源重新生成。

| 范围 | 文件数 | 逻辑字节数 | 主要类型 |
|---|---:|---:|---|
| 旧项目 `docs` | 314 | 19,180,463 | 225 Markdown、37 PNG、22 JSON、17 HTML |
| 旧项目 `work` | 227 | 2,650,400 | 158 Markdown、32 Python、30 JSON、7 JavaScript |
| 旧项目 `outputs` | 341 | 762,880,023 | 189 JSON、83 PNG、11 NDJSON、6 JSONL、5 XLSX 等 |
| 旧项目 `data/output` | 43 | 173,688,489 | CSV、XLSX、日志和运行输出 |
| 项目当前 `.codex/skills` | 30 | 564,574 | 数据 CSV、Python、入口和缓存 |
| 用户非系统自定义 Skills | 137 | 1,133,435 | Remotion 文档、示例及资源 |

数据为本轮文件快照，包含隐藏工作文件；逻辑大小与磁盘占用不同。系统 Skills 和安装插件不进入清理建议。

## 2. 清理登记

每个候选都须先确认具体动作、保留物和必要备份，不能以本表代替删除授权。

| ID | 绝对路径 | 分类与证据 | 建议、风险与待确认 |
|---|---|---|---|
| C01 | `/Users/bjn00003/BDHub/01-BDSystem-V2/CLAUDE.md` | 冲突兼容入口。第 3 行称不维护第二套事实，但 14–17 行仍写只启用 MX、浏览器 Profile `[2]`、非 MX 入口退役，与当前 AGENTS 和七市场/Pure HTTP `[1,2,6]` 冲突。还要求每轮顺序读四份资料。 | 优先修订候选：未来获准后只保留链接型兼容入口。当前不改。禁止把这些过期底线带入新项目。 |
| C02 | `/Users/bjn00003/BDHub/01-BDSystem-V2/docs/README.md` | 过时索引。第 3、26–28 行称日期文档均已归档、事实只用无日期文件、改动同步三份文档；当前 AGENTS 实际引用日期架构/验收，且要求只更新失真资料。 | 保留并登记修订，不删除。按当前入口重新整理需用户确认；不得据其日期规则清理现役证据。 |
| C03 | `/Users/bjn00003/BDHub/01-BDSystem-V2/AGENTS.md` | 有效项目入口，7,362 字节；明确底座、业务规则和按任务读取资料。 | 旧项目保留。新项目只摘录必要接口与边界，单独维护短入口；不复制旧项目全部运行维护指令。 |
| C04 | `/Users/bjn00003/BDHub/01-BDSystem-V2/docs/current-system-truth.md`；`/Users/bjn00003/BDHub/01-BDSystem-V2/docs/ai-handoff.md` | 仍被入口直接引用，各约 92 KB；新记录与多轮旧快照并列，增加阅读负担，也保存了关键能力证据。 | 保留原件。新项目只带注明来源日期的当前摘要。可另提历史整理方案，不直接覆盖原记录。 |
| C05 | `/Users/bjn00003/BDHub/01-BDSystem-V2/docs/archive/` | 已正式归档，159 文件、3,863,680 字节。README/MANIFEST 明确退役理由，含旧视觉、superpowers 和 vendor skill。 | 维持归档，不带入新项目；收益小且有追溯价值，无立即删除理由。移除需明确历史保留要求。 |
| C06 | `/Users/bjn00003/BDHub/01-BDSystem-V2/work/` | 历史研究证据，当前 docs/README 仍指向 Evidence→Finding→Path；含探针、报告和 SDK 快照。 | 保留历史，不作为新系统默认命令。不能因为脚本未被生产 import 就删除复现材料。 |
| C07 | `/Users/bjn00003/BDHub/01-BDSystem-V2/outputs/01a01865-ac01-75b2-b166-37f5b5b633f5/.work/final_inspect.ndjson` | 大型检查中间件，499,440,626 字节。同目录存在工作簿构建/检查脚本，目录外有最终 Excel。 | 优先移除候选；先确认交付已留存、无需完整转储、必要校验可重做。未验证可无损删除，不能顺带清空目录。 |
| C08 | `/Users/bjn00003/BDHub/01-BDSystem-V2/outputs/01a01e7e-5316-7163-9130-bfc0c1964b66/.work/live_lead_export_inspect.ndjson`；`/Users/bjn00003/BDHub/01-BDSystem-V2/outputs/01a01e7e-5316-7163-9130-bfc0c1964b66/.work/export_inspect.ndjson` | 检查转储，分别 61,009,644、37,373,899 字节；同目录有生成数据、脚本和预览，外层有两份实际 Excel。 | 与 C07 合计 597,824,169 字节，约 570 MiB；是优先调查的空间候选，不是已确认垃圾。须确认保留脚本、最终交付和精简校验结果。 |
| C09 | `/Users/bjn00003/BDHub/01-BDSystem-V2/outputs/01a01865-ac01-75b2-b166-37f5b5b633f5/.work/report_data.json` | 可复现输入，59,036,828 字节；`build_workbook.mjs:8` 明确读取，`extract_0815.py:14` 产生。 | 与检查转储分开。可能是当时唯一快照；建议保留或获准后归档，不能声称今天重新查询可还原。两处 UUID 目录的最终 XLSX 同样保留。 |
| C10 | `/Users/bjn00003/BDHub/01-BDSystem-V2/data/output/uk_collector_2026-07-30/` | 真实数据交付，主 CSV 约 169 MB；历史交接称供另一采集项目接收。 | 确认外部项目已接收且无需原件后才考虑归档。`/Users/bjn00003/BDHub/01-BDSystem-V2/data/output/达人信息库.xlsx` 仍是 `config.py:566` 默认输出路径，不能整目录清空。 |
| C11 | `/Users/bjn00003/BDHub/01-BDSystem-V2/outputs/im-demand-analysis-20260908/` | 重要研究基础，约 20 MB；当前蓝图明确引用 report、scenario_catalog、execution_contract。 | 保留。新项目仅带场景结构、经筛选脱敏的正反例和来源索引，不全量复制原对话；旧统计不标实时。 |
| C12 | `/Users/bjn00003/BDHub/01-BDSystem-V2/outputs/ai-native-ui-20260911/` | 本轮未验收原型，7 文件、6,616,542 字节。用户最新要求转为先深度调查。 | 停止继续完善，保留为未批准参考；是否归档/移除待用户决定。不作为新项目既定视觉方案。 |
| C13 | `/Users/bjn00003/BDHub/01-BDSystem-V2/outputs/im-http-batch-20260906/`；`/Users/bjn00003/BDHub/01-BDSystem-V2/outputs/intake-parallel-20260911/` | 当前交接/运行报告仍引用的验收与未知结果证据；前者还被诊断脚本默认引用。 | 保留。不能将真实发送、冻结范围、unknown 核验材料作为普通生成垃圾处理。新项目引用精简结论和证据地址即可。 |
| C14 | `/Users/bjn00003/BDHub/01-BDSystem-V2/.codex/skills/ui-ux-pro-max/scripts/__pycache__/` | 两个 Python 编译缓存，原始脚本保留。 | 低风险可重建缓存候选；本轮不删除，整体 skill 仍有用。 |

截图也不一律无用：`docs/architecture/assets/product-library-audit-20260826/` 与 `product-pools-v52-audit-20260827/` 被现有商品架构文档逐图引用，应与对应报告一并判断，不能单独依据大小删除。

## 3. 有效规则入口与 Skills 审计

全局 `/Users/bjn00003/.codex/AGENTS.md` 当前是用户的沟通、执行、验证、工具与规则来源偏好；检查的上级目录没有额外 AGENTS。它继续适用于新项目，无需再复制一份。

项目当前只有 `/Users/bjn00003/BDHub/01-BDSystem-V2/.codex/skills/ui-ux-pro-max/`。入口明确：局部修正无需完整设计系统；`--persist` 仅在交付需要时使用；检索建议不覆盖产品决策。没有发现强制换栈、强制重设计或模型版本锁定，建议保留，新项目按需选择。

全局非系统自定义只有 `/Users/bjn00003/.codex/skills/remotion-best-practices/`。入口已允许按任务加载、不要求另行请求 render、既有授权内继续；没有特定 GPT/Claude 版本依赖。发现两项可改善之处：

| ID | 路径与证据 | 建议 |
|---|---|---|
| S01 | `/Users/bjn00003/.codex/skills/remotion-best-practices/remotion-markup/REFERENCE.md:24` 无条件要求 Interactivity；`remotion-create/REFERENCE.md:36` 却限定可编辑交付才需要。 | 后续获准后统一条件语义，不给普通视频强加可编辑控件。`video-editing.md` 的硬编码/禁 `.map()` 已限定于 Studio 可编辑时间线，不应泛化。 |
| S02 | `/Users/bjn00003/.codex/skills/remotion-best-practices/remotion-maps/` 与 `/Users/bjn00003/.codex/skills/remotion-best-practices/remotion-markup/remotion-maps/`：31/32 文件哈希相同，重复约 487 KB；唯一差异为相对链接，且两处均有入口引用。 | 可以提议统一引用后去重；不能直接删任一目录。全局技能修改需用户确认，不卸载。 |

旧归档 skill `/Users/bjn00003/BDHub/01-BDSystem-V2/docs/archive/frontend-legacy-2026-08-03/vendor-codex-skills/redesign-existing-projects/SKILL.md` 写死旧配色、桌面、vanilla 栈和较广回归，引用旧截图。它是归档资料，不是当前活动 skill；不带入新项目。

## 4. 官方建议与本机会话路径的区别

本轮主任务已核对 [OpenAI Codex Skills 官方文档](https://developers.openai.com/codex/skills/)：仍推荐范围集中的 Skills 和渐进披露，先发现名称/描述等元数据，触发后再加载正文。文档描述技能元数据预算最多为上下文的 2%，上下文大小未知时使用 8,000 字符；该数字不是要求把每份 SKILL.md 正文全部预加载，也不是删除 Skills 的依据。**未核实官方 GPT-6 禁用或建议停用 Skills 的说法。**


**GPT-6 的直接官方依据补充：** 本轮主任务于 2026-09-11 实际读取 [Using GPT-6 Astra](https://developers.openai.com/api/docs/guides/latest-model) 的 Instruction following 段。该段说明模型对 Skills、AGENTS.md 等指令更敏感，并写道：“strongly recommend auditing skills and other files accessible to your model for instructions that could influence its behavior”。这直接支持审计强制流程、冲突规则和过时约束；它不支持整体停用 Skills。应与 Skills 官方页的 focused skills、progressive disclosure 一并理解：保留有用的专门知识，缩小触发范围和加载量，清理不再适用的指令。上述模型依据也不授权本轮修改或卸载任何技能。

官方当前说明 `.agents/skills` 路径；本机会话实际暴露的技能来自 `.codex/skills` 及安装插件。两者须分别记述，不能凭文档路径推断当前配置已迁移：

| 本轮实查绝对路径 | 存在 | symlink | 说明 |
|---|---|---|---|
| `/Users/bjn00003/BDHub/01-BDSystem-V2/.agents/skills` | 否 | 否 | 父 `.agents` 也不存在 |
| `/Users/bjn00003/.agents/skills` | 否 | 否 | 父 `.agents` 也不存在 |
| `/Users/bjn00003/BDHub/01-BDSystem-V2/.codex/skills` | 是 | 否 | 当前仅 ui-ux-pro-max |
| `/Users/bjn00003/.codex/skills` | 是 | 否 | remotion-best-practices 和排除在本审计外的 `.system` |
| `/Users/bjn00003/BDHub/BDHub-Agent/.agents/skills` | 否 | 否 | 本次检查时尚未创建 |
| `/Users/bjn00003/BDHub/BDHub-Agent/.codex/skills` | 否 | 否 | 本次检查时尚未创建 |

本轮未改配置、迁移目录或创建 symlink。项目型业务 Skills 是否需要安装及目标路径，应在新项目明确需求和实际运行支持后决定。

## 5. 新项目仅带入的必要资料

1. 用户确认的 MX+BR+IT、TikTok IM 优先、完整合作闭环与自主范围；不把旧单市场限制带入。
2. market+OEC/PID/offer 的身份契约、只读查询/经授权执行适配接口、能力证据及日期。
3. 发送组件/intent 的唯一结果、unknown、暂停、账号亲和与新旧执行归属互斥。
4. 各市场佣金、样品、建链规则的有效来源，不复制凭证、全部配置和生产数据库。
5. 精选历史 IM 场景、参考答案结构、脱敏正反例与评估合同，不复制全部对话。
6. 当前蓝图中经本次方向确认的设计；原“同仓模块”方案须改为独立项目的适配边界，不能原样复制后称已完成独立化。

新项目保存必要摘要、接口合同和来源索引；旧文档全集、输出全集、旧技能全集及未批准 UI 原型均不自动带入。仅登记的删除或全局技能修订，仍待用户对具体条目确认。
