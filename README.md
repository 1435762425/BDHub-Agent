# BDHub-Agent

独立的 Agent 驱动达人经营系统。目标是以 1–2 人运营墨西哥、巴西和意大利的一发、二发及合作服务，逐步迁入必要能力与历史数据，最终可替代旧 BDHub。

打开 **[新匹配工作台](http://127.0.0.1:5198/opportunities?mode=matching)**，或进入[本地合作工作台](http://127.0.0.1:5198/workspace?mode=local)、[界面演示](http://127.0.0.1:5198/overview)。

新增 **[意大利真实数据离线测试](http://127.0.0.1:5198/opportunities?mode=matching&dataset=italy)**：独立导入5商品、48达人、50条同品出单证据，双向回放完整，已准备48份有界评审资料。没有真实发送或模型调用；一发画像与商业方案缺口明确保留。[测试结果与复现](docs/implementation/italy-offline-matching.md)。

**[已有画像自动分析](http://127.0.0.1:5198/opportunities?mode=matching&dataset=italy-profiles)** 已补齐3,978人的多类目与经营数据，自动分析1,543个组合；忽略画像年龄，价格带和形式仅参考，人工反馈可选。全量查询、排序和1,377份资料包已自动验证，未调用业务模型或发送。[当前政策与测试](docs/implementation/existing-profile-auto-analysis.md)。

**[意大利二发工作台](http://127.0.0.1:5198/workspace?mode=second)** 已把48位达人/50条同品关系合并成48条意语稿件，支持冻结、持久模拟队列、暂停和未知核验。[结果台](http://127.0.0.1:5198/results?mode=second)保留47条模拟接收及1条故障用例未知；真实发送0。[范围、来源与复现](docs/implementation/italy-second-pilot.md)。

结构化匹配已经可运行：商品找达人、达人找商品、精确同款证据、可解释候选与最多5商品的关系评审包。默认72商品/360达人均为合成事实；1万商品/5万达人的三市场分层离线基准P95约32ms，本轮模型调用和计费token为0。[实现与验收](docs/implementation/structured-matching-v1.md)。

新增本地持久运行切片：示例消息、关系控制、任务、上下文和逐组件回执进入独立 SQLite，Worker 在页面关闭后仍可处理，重新启动进程后从原记录继续。当前采用确定性判断和本地模拟平台，未调用真实模型或 TikTok。[运行范围与恢复验证](docs/implementation/local-runtime-v1.md)。

原型基于真实 **TailAdmin Next.js Free 2.3.0（MIT）**，缺少的业务页面自行实现。七个入口贯通经营目标、二发/一发机会、合作会话、人工决定、MX 历史接续、话术策略与结果查看。原七页和本地运行仍为示例；意大利匹配数据是单独标识的历史真实快照。尚未接入真实模型、生产业务数据库或平台执行。旧系统业务和运行保持只读；已授权资料/Skills清理已经结束，此前两套生成 UI 均未采用。

## 启动与体验

在 `apps/web` 执行：

```sh
npm ci
npm run build
npm run start
```

本地运行还需在另一个终端、同一目录执行 `npm run runtime:worker`。数据库位于新项目 `var/runtime.sqlite`，不清除历史记录。

开发时使用 `npm run dev`；开发和生产预览共用 `127.0.0.1:5198`，同一时间只启动一种。完整命令与演示流程见 [前端说明](apps/web/README.md)。建议先点击页面中的“体验流程”，依次体验 Sofía 的二发合作、Luca 的人工决定和 Camila 的历史接续。“重置演示”可恢复初始示例。

## 最小阅读入口

| 内容 | 入口 |
| --- | --- |
| 产品需求与完成标准 | [详细 PRD](docs/PRD.md) |
| 已确认条件、待决策问题 | [决策登记](docs/DECISIONS.md) |
| 架构取舍与落地顺序 | [架构方案](docs/architecture/system-design.md) |
| 匹配实现与规模验证 | [结构召回实现](docs/implementation/structured-matching-v1.md) |
| 新匹配体系与 token 预算 | [匹配设计与测算](docs/architecture/matching-and-token-budget.md) |
| 单 Agent、按需协作、上下文与 Skills | [Agent 运行合同](docs/architecture/agent-runtime.md) |
| Mac 与服务器承载判断 | [部署与容量](docs/architecture/deployment-capacity.md) |
| 必要旧接口与事实来源 | [接口迁入清单](docs/contracts/legacy-interface-inventory.md) |
| 本地持久运行 | [范围、命令与恢复验证](docs/implementation/local-runtime-v1.md) |
| 视觉与交互验收 | [验收报告及当前点验限制](design-qa.md) |
| 前端原型与来源 | [运行/路由/演示流程](apps/web/README.md) · [源码清单](apps/web/TAILADMIN-SOURCE.json) · [第三方许可](apps/web/THIRD_PARTY_NOTICES.md) |
| 已选 TailAdmin 的设计推进 | [设计执行稿](design/tailadmin-design-brief.md) · [14视图映射](design/tailadmin-page-map.md) · [许可核对与历史研究](docs/research/tailadmin-license-review.md) |
| UI 模板研究归档 | [图文选择目录](design/template-catalog.html) · [模板研究](docs/research/ui-template-options.md) |
| 公开 Agent 案例与反证 | [案例复核](docs/research/agent-cases-review.md) |
| 旧文件和自定义 Skills 整理 | [清理结果与恢复清单](docs/research/cleanup-register.md) |

## 项目状态

- 已确认：独立目录与 Git 仓库；旧 BDHub 不修改；三市场、TikTok IM 首发、后续 WhatsApp；范围内自主经营；1–2 人；无固定业务日触达上限。
- 已完成：旧资料与 Skills 只读审计、必要接口盘点、真实模板及官方 Agent 案例研究、PRD/架构设计、七路由前端原型、单关系本地持久运行切片。
- 当前 Mac：持续开机运行。服务器迁移不预设为必要条件，按实际平台会话与负载验证决定。
- 已确认架构基线：关系 Agent＋程序调度与执行＋按需专家；长期业务数据自控，模型只取当前必要上下文。
- 已选 UI：T03 TailAdmin Next.js 免费 MIT 版，缺页自建；来源和版本已固定，无 Pro 购买依赖。
- 匹配设计：按1k–10k商品、上万达人重新设计，结构/精确PID召回＋关系级模型决策＋增量特征复用；已完成预算工具、结构召回与本地评审包；真实数据质量、生产匹配器与模型选择仍待验证。
- 未决：具体 Agent 运行框架、日预算与部署切换条件；不得用前端模拟或研究建议冒充生产能力。
- 指定资料与 Skills 清理已完成，保留可恢复归档。业务执行器、账号、商业规则和模型费用范围须在试点启用时明确。

文档区只保存筛选后的需求、契约和证据索引，应用代码集中在 `apps/web`。旧项目的长篇交接、历史设计图、检查转储和原始业务数据不整包复制。默认先读当前需求与相关决策，再按任务读取一份架构或接口文件。

## 查看模板目录

本机预览：[模板选择目录](http://127.0.0.1:5196/design/template-catalog.html)。也可直接打开 design/template-catalog.html；图片来自官方公开预览资源，需要网络。页面中的“记下此选项”只保存到本机浏览器，须把生成的选择文字发回对话，不会自动通知或购买。

需要重开静态预览时，在本仓库执行 `python3 -m http.server 5196 --bind 127.0.0.1`。这只服务研究文档与模板目录，不启动 BDHub 后端。
