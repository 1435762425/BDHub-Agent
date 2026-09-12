# BDHub-Agent 前端原型

访问 **[匹配工作台](http://127.0.0.1:5198/opportunities?mode=matching)**、**[本地运行](http://127.0.0.1:5198/workspace?mode=local)** 或[界面演示](http://127.0.0.1:5198/overview)。这是基于 TailAdmin Next.js 免费 MIT 版的可操作界面原型，用于验证经营流程、信息组织和状态表达。

原七页演示使用示例；意大利匹配、二发及达人库已使用真实来源资料。达人库可显式提交意大利画像只读刷新，复用旧账号的HTTP认证与验证实现，不修改旧业务库、不发送消息；其余平台执行仍为模拟。原七页演示仍使用 localStorage；新增本地运行工作台使用独立 SQLite 和 Worker，保存事件、计划、控制、任务、组件尝试与回执。没有生产认证、真实连接器或生产 Agent 框架。

## 运行与验证

需要 Node.js >=22.18 及 npm，以使用内置 SQLite 和 TypeScript 运行支持。本轮验证环境为 Node.js 26.7.0；依赖固定在 `package-lock.json`。

```sh
npm ci
npm run dev
```

生产模式预览：

```sh
npm run build
npm run start
```

本地运行需另开一个终端执行 `npm run runtime:worker`。Worker 默认使用 `../../var/runtime.sqlite`，与 Web 共用独立数据库；支持 `BDHUB_AGENT_RUNTIME_DB`、`--db`、`--once`、`--interval`。不安装系统自启服务。

两种 Web 服务都仅监听 `127.0.0.1:5198`，不要同时启动。源码检查与模拟状态测试：

```sh
npm run typecheck
npm test
```

`npm test` 当前运行218项检查；`npm run test:matching`运行79项匹配/导入/迁移/自动分析/人工评审检查，`npm run test:runtime`只运行本地运行底座相关检查。覆盖进程恢复、关系接管、版本/租约、重复入站、上下文隔离、意大利字段适配与逐组件结果核验。它不验证真实平台或模型效果。浏览器与视觉验收记录见仓库根目录 `design-qa.md`。

## 七个页面

| 路由 | 入口 | 当前可体验内容 |
| --- | --- | --- |
| `/overview` | 今日经营 | 市场切换、经营指标、目标进度、合作动态、需要人决定、关系下钻 |
| `/goals` | 经营目标 | 新建/编辑、必填校验、影响预览、保存草稿、启用、按范围暂停 |
| `/opportunities` | 机会与货盘 | 二发、一发、商品与待准备视图；筛选、分页、选择、依据抽屉、归并到合作事项 |
| `/workspace` | 合作工作台 | 同一达人的多个事项、原文与辅助理解、接管/交还、人工消息、内部备注、决定、事件模拟及未知核验 |
| `/results` | 效果与质量 | 接收、采用、未知结果分开查看；市场汇总、模型费用与收益证据边界 |
| `/agents` | Agent 与知识 | 职责与能力、三种话术模式和三市场预览、版本、知识/历史核实及接续、离线回放 |
| `/settings` | 系统设置 | 资源设置表单与校验、市场/渠道接入状态和运行原则 |

全局提供搜索（`⌘/Ctrl K`）、市场选择、最近动态、明暗主题、导航折叠和体验导览。表单、抽屉、弹层及页面共享同一份模拟关系状态。

## 新匹配工作台

侧栏“机会与货盘”进入新匹配入口。支持商品/达人双向分页检索，显式选择一发/二发来源，查看事实、完整Offer及缺项，准备单关系少量候选评审包；不调用模型或发送。数据保存在 `var/matching.sqlite`，可通过 `BDHUB_AGENT_MATCHING_DB` 设置独立验证库。

`npm run test:matching` 运行匹配检查，`npm run benchmark:matching -- --large` 使用临时合成库验证1k/10k与10k/50k规模，不读旧库。详细口径见 [结构匹配实现](../../docs/implementation/structured-matching-v1.md)。

意大利真实离线入口：`/opportunities?mode=matching&dataset=italy`。首次运行`npm run import:italy`，固定读取旧项目意大利研究快照、写入新项目`var/matching-italy.sqlite`并运行已知证据回放。脚本不接受自选旧数据库写入路径，不调用旧业务服务/模型/发送器；缺资料库时API报错，不生成示例冒充。默认`dataset=demo`保留原合成数据和待恢复请求。[结果与字段边界](../../docs/implementation/italy-offline-matching.md)。

画像一发实验：`/opportunities?mode=matching&dataset=italy-profiles`。先在项目根用旧Python环境执行新脚本`scripts/export-italy-profile-signals.py`（本机PG只读），再执行`npm run import:italy-profiles`导入固定OEC画像、全部具名类目与经营指标；写入独立`var/matching-italy-profiles.sqlite`，不导入销量边或合并身份。自动分析不等待人工判断；人工反馈可选。[当前分析与复现](../../docs/implementation/existing-profile-auto-analysis.md)。

## 本地运行体验

从“合作工作台 → 进入本地运行”进入。Sofía 和 Pedro 已保留本轮真实本地接口/进程恢复的验收示例，均为每个组件 1 次模拟提交；Pedro 的运行记录中可查看回执丢失和原结果核验。可以继续模拟新的明确请求、接管、暂停、历史存档或延后复查。

本地运行不复用原演示的“重置”按钮。未确认的 HTTP 请求编号和命令临时保留在当前标签页 sessionStorage，刷新后沿用同一编号核实；业务事实只以 SQLite 为准。详见 [运行说明](../../docs/implementation/local-runtime-v1.md)。

## 原界面演示路径

1. **二发到结果**：在“体验流程”打开 Sofía，查看证据和商品事项；“模拟推进下一步”产生平台接收状态；再从“演示事件”确认方案采用。平台接收与采用是两个独立事件。
2. **人工决定**：打开 Luca 的合作，查看超出当前佣金条件的要求与可选方案，提交当前事项的决定，再交还 Agent。
3. **未知核验**：打开 Pedro 的商品卡事项，使用“演示事件”补入原意图的核验证据，观察状态变化且不追加一次发送。
4. **MX 历史接续**：在“Agent 与知识 → 知识与历史”核实 Camila 的当前事实，接回可行动事项。核实历史本身不会发送消息。
5. **自建目标**：新建目标，选择市场、方向与资源边界，查看预览后保存或启用，再测试暂停范围。

所有操作可使用顶部“重置演示”回到初始场景。

## 数据和实现位置

- 模拟业务状态：`src/features/bdhub/model.ts`；共享状态及浏览器存储：`store.tsx`。
- 页面、组件与交互：`src/features/bdhub/`；Next.js 路由：`src/app/`。
- 浏览器业务状态键：`bdhub-agent-tailadmin-prototype-v1`。只在相同浏览器来源下保留，不跨浏览器/设备同步；存储不可用时提示并仅保留当前页面内存状态。
- 关系的人工控制、拒联和未知动作保持独立；单个事项处理完成不直接计为方案采用、内容产出或收入。
- 后续接真实能力时应替换数据/执行适配层，并依据 PRD 验证身份、权限、并发、监听补回和持久化，不能把浏览器 reducer 当作生产执行保障。

## 模板及资源

使用免费仓库 `TailAdmin/free-nextjs-admin-dashboard` 的 2.3.0，固定提交 `d3526b35fb7e579a4585129fe6eaa47f54ec9a0b`。按需导入主题、基础 UI、图标及演示素材；业务页面和共享对话框自行实现。版权见 [LICENSE](LICENSE)，逐文件来源与改动见 [TAILADMIN-SOURCE.json](TAILADMIN-SOURCE.json)，字体及依赖说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 意大利二发预演

`/workspace?mode=second`和`/results?mode=second`使用真实来源稿件与独立本地模拟器。执行`npm run pilot:italy-second -- --rehearse`导入、测试和状态化续跑，再执行`npm run second:worker`保持队列处理。前置准备调查由新项目`scripts/export-italy-second-readiness.py`只读导出；不复制凭证、不真实发送。同一轮完成/未知不重发，暂停不会误恢复。详见[二发试点](../../docs/implementation/italy-second-pilot.md)。

## 达人库与画像刷新

进入 [达人库](http://127.0.0.1:5198/creators)，查看当前/历史名字、OECID、最新字段与另行标注的历史值。匹配与二发详情可打开稳定档案，原冻结稿和未知结果保持不变。

在另一终端运行 `npm run identity:worker`，页面的“刷新画像”会按已存 OEC 提交持久任务，只读取 IT 平台画像。关闭/重载页面可继续查看结果，重复请求沿用原编号；同达人进行中的刷新归并。刷新数据库独立于匹配和二发库，不自动按旧 handle 重新认人。

本轮未安装自启服务，机器或进程重启后需重新运行 Web 与画像 Worker。详见[运行及真实验收](../../docs/implementation/creator-identity-ui.md)。

## 批量扩充达人池

达人库的“批量添加达人”支持最多500项handle/@handle/主页链接，先本机预览，再提交真实只读发现。已存在的OEC归入原档案，未匹配线索复用；Find成功而Profile失败仍保留身份。`npm run identity:worker`现同时顺序处理OEC刷新和handle发现，不需要另开同账号Worker。暂停、继续未处理项及页面恢复均已实测，详见[批量入池](../../docs/implementation/creator-discovery.md)。

## 新画像参与匹配

`italy-profiles` 现按身份库追加观察增量更新，不需新增Worker；页面可见时每5秒读取同步版本，并将过时结果标明，用户明确重新分析后切换新结果。旧包和人工判断保留。可从达人档案“分析适合商品”按canonical身份进入，或运行 `npm run sync:identity-profiles -- --verify` 追上水位并验证。旧历史批次导入脚本不再用于覆盖已同步库。详见[同步合同与验收](../../docs/implementation/registry-profile-matching-sync.md)。
