# BDHub-Agent：真实 Web 模板候选

核实日期：2026-09-11。以下为可取得代码或商业授权的真实 Dashboard / Admin / CRM 模板，不包含 Linear、Attio 等仅能参考外观的产品主页，也不沿用此前被否定的自创 UI。

本报告通过公开官方网页、官方/作者 GitHub README、package.json、许可证和默认分支提交进行只读核实。**本报告作者没有在浏览器亲眼查看或截图任何候选**；HTTP 200 表示页面或前端入口可以获取，不等于交互已经运行、视觉已经验收。没有登录、购买、克隆仓库、下载执行源码或调用业务 API。最终模板由用户看实际页面后选择。

## 先比较七个主候选

| 编号 | 准确名称 / 类型 | 具体 Demo | 费用与许可 | 访问状态 / 视觉状态 |
| --- | --- | --- | --- | --- |
| **T01** | **Shadcn Admin Dashboard — satnaing**；React 管理台模板，非 shadcn 官方模板 | [总览](https://shadcn-admin.netlify.app/) · [会话](https://shadcn-admin.netlify.app/chats) | 免费，MIT | `/chats` HTTP 200；客户端应用，实际渲染待看；未截图 |
| **T02** | **Next Shadcn Dashboard Starter — Kiranism**；Next.js 应用起步模板 | [Dashboard](https://shadcn-dashboard.kiranism.dev/dashboard) · [作者列出的 Chat](https://shadcn-dashboard.kiranism.dev/dashboard/chat) | 免费，MIT；集成服务另有自己的使用条件 | Dashboard 本次重定向到 Clerk 登录页；未登录、未见后台；未截图 |
| **T03** | **TailAdmin Next.js**；免费版与 Pro 版分别提供 | [免费版](https://nextjs-free-demo.tailadmin.com/) · [Pro CRM](https://nextjs-demo.tailadmin.com/crm) · [Pro 会话](https://nextjs-demo.tailadmin.com/chat) | 免费代码 MIT；Pro 为单独商业许可 | 三个 Demo 均 HTTP 200，页面标题/导航可读；视觉待看；未截图 |
| **T04** | **Tremor Dashboard**；完整 SaaS / Internal Tool 数据应用模板 | [总览](https://dashboard.tremor.so/overview) · [数据详情](https://dashboard.tremor.so/details) | **当前完整版本免费，MIT** | 总览 HTTP 200，可读图表/用量/设置内容；视觉待看；未截图 |
| **T05** | **Untitled UI React — Dashboard page examples**；完整页面模板库，非一套接好后端的 Admin 应用 | [Dashboard 页面集合](https://www.untitledui.com/react/components/dashboards) · [Dashboard 01 全屏官方预览](https://www.untitledui.com/react/iframe/dashboards/dashboard-01) | 开源组件 MIT；此类高级页面示例属于 Pro 商业许可 | 页面集合 HTTP 200，全屏预览 HEAD 200；源码按钮需相应权限；视觉待看；未截图 |
| **T06** | **Tabler**；Bootstrap HTML Dashboard UI Kit / 模板 | [CRM](https://preview.tabler.io/dashboard-crm.html) · [会话](https://preview.tabler.io/chat.html) · [完整 Demo](https://preview.tabler.io/) | 免费，MIT | 总览、CRM、会话 HTTP 200；视觉待看；未截图 |
| **T07** | **Mantine Analytics Dashboard — DesignSparx**；第三方 Mantine Admin 模板，非 Mantine 官方应用 | [默认 Dashboard](https://mantine-analytics-dashboard.netlify.app/dashboard/default) · [会话](https://mantine-analytics-dashboard.netlify.app/apps/chat) | 免费，MIT | 两页 HTTP 200；SSR 可读应用框架，数据区有加载/空态，最终客户端体验待看；未截图 |

## 技术、维护与适配

### T01 — Shadcn Admin Dashboard

- **来源与许可：** [作者仓库](https://github.com/satnaing/shadcn-admin) · [MIT](https://github.com/satnaing/shadcn-admin/blob/main/LICENSE)。当前 package 为 React 19、Vite 8、Tailwind 4、TypeScript 6、TanStack Router/Table、Recharts；README 说明基于 shadcn/ui 并有定制组件。
- **维护证据：** 默认分支最近提交 [e16c87f213a5](https://github.com/satnaing/shadcn-admin/commit/e16c87f213a5ba5e45964e9b67c792105ec74d26)，2026-06-11，Clerk 依赖更新。仓库 pushed_at 更晚，不用它冒充主分支功能更新。
- **对应 BDHub：** 已有 chats、tasks、users、account/appearance/notifications/settings 路由；表格、事项列表、关系会话、配置页可以在同一壳中延展。README 提供明暗模式、响应式和 RTL。
- **不足：** 作者定位是 Dashboard UI，认证仅部分接入；不是达人业务系统。AI 关系负责人、商品证据、执行状态都需加入。部分组件被作者改过，后续 shadcn CLI 覆盖更新需要保留定制。

### T02 — Next Shadcn Dashboard Starter

- **来源与许可：** [作者仓库](https://github.com/Kiranism/next-shadcn-dashboard-starter) · [MIT](https://github.com/Kiranism/next-shadcn-dashboard-starter/blob/main/LICENSE)。当前 Next.js 16.2、React 19、Tailwind 4、TypeScript，shadcn/ui 基于 Base UI；TanStack Query/Table/Form、Zod、nuqs、Clerk。
- **维护证据：** [7705dfc0d138](https://github.com/Kiranism/next-shadcn-dashboard-starter/commit/7705dfc0d13889e45c26a55ad5908da6a7a9a605)，2026-08-24，Clerk 依赖升级。
- **对应 BDHub：** README 明确包含商品/用户表格、校验表单、Kanban、Chat、通知、团队与权限结构；适合需要真实列表查询、表单状态和应用分层的起点。
- **不足：** 当前公开入口要求登录，因此不列为第一轮无需登录的视觉比较页。它的 AI Chat 是**预写对话流式演示**，不是实际 Agent；组织、计费、Clerk/Sentry 等依赖也不是 BDHub 必须保留的业务。作者对 production-ready 的宣传不等于本轮实现验收。

### T03 — TailAdmin Next.js

- **来源：** [免费仓库](https://github.com/TailAdmin/free-nextjs-admin-dashboard) · [MIT](https://github.com/TailAdmin/free-nextjs-admin-dashboard/blob/main/LICENSE) · [Pro 购买页](https://tailadmin.com/pricing) · [Pro 商业许可](https://tailadmin.com/license)。免费包当前 Next.js 16、React 19、Tailwind 4、TypeScript，使用 ApexCharts。
- **维护证据：** 免费默认分支 [d3526b35fb7e](https://github.com/TailAdmin/free-nextjs-admin-dashboard/commit/d3526b35fb7e579a4585129fe6eaa47f54ec9a0b)，2026-04-28，package/README changelog 更新，当前包 2.3.0。这不是 Pro 私有源码的最新提交证明。
- **对应 BDHub：** Pro 可直接比较 CRM、chat、data tables、form layouts、AI settings 等完整页面组合，覆盖指标、线索、会话与配置。免费版只有基础电商总览、简单表格、表单和图表，不能把 Pro Demo 的功能计入免费代码。
- **费用边界：** 本次价格页单技术栈显示 Starter 59 美元、Business 119 美元、Extended 299 美元的一次性促销价；实际购买前以价格页为准。许可分别为 1/3/10 席位、3/10/不限项目；Starter/Business 明文不适用于 SaaS End Product，Extended 可用于。**不能把免费 MIT 套在 Pro 页面上。**
- **不足：** 页面数量多，未自动形成统一的 AI 合作闭环；需要精简信息架构并补关系上下文。收费档位按真正使用源码/设计资产的人和最终产品类型确认，不简单等同于 1–2 位运营账号。

### T04 — Tremor Dashboard（当前完整 MIT 版）

- **来源与许可：** [完整模板仓库](https://github.com/tremorlabs/template-dashboard) · [MIT](https://github.com/tremorlabs/template-dashboard/blob/main/LICENSE) · [当前免费声明](https://blocks.tremor.so/) · [模板与许可](https://blocks.tremor.so/license)。当前 Next.js 14.2、React 18、Tailwind 3、TypeScript、TanStack Table、Recharts/Radix。
- **维护证据：** [ac673531b124](https://github.com/tremorlabs/template-dashboard/commit/ac673531b124244b75fef5c750df1fa9ee783d4a)，2025-10-10，依赖更新。
- **对应 BDHub：** 现成图表、筛选/批量编辑、成本用量、设置/用户管理、明暗模式；适合经营效果、机会表格和费用资源页面。完整应用壳可扩展合作业务。
- **不足：** 数据分析和 SaaS 用量导向明显，没有现成达人会话闭环。当前栈和最近提交比其他候选老，上线选型需要评估升级工作量。
- **旧新区别：** `template-dashboard-oss` 老仓库是 Apache-2.0，其独立 OSS Demo 本次 TLS 连接失败；不要据此认为当前完整模板不可用或仍收费。当前站点明确写着 **“Blocks & Templates are now free and open-source”**，完整仓库为 MIT。

### T05 — Untitled UI React 页面模板

- **来源：** [页面模板](https://www.untitledui.com/react/components/dashboards) · [购买来源](https://www.untitledui.com/react) · [开源组件仓库](https://github.com/untitleduico/react) · [商业许可](https://www.untitledui.com/license)。React 19、Tailwind 4、TypeScript、React Aria；仓库同时配置 Next.js/Vite 工具。
- **维护证据：** 开源组件默认分支 [c981a73bcd6b](https://github.com/untitleduico/react/commit/c981a73bcd6b6c68d2a54070f20f020191212828)，2026-08-26，日历布局修复。它只证明公共组件维护，不证明 Pro 的私有发布日期。
- **对应 BDHub：** 官方具有多种完整 analytics/customer/sales/organization dashboards，以及 tables、settings、表单组件；适合先选稳定视觉系统，再组装机会、关系、策略和结果页面。支持 React Aria 的交互基础。
- **许可区别：** 公共仓库代码 MIT；高级页面需 Pro。商业许可允许个人/商业最终产品，限制原始资产和源码作为模板/库对外再分发；Solo 单用户、Studio 最多 8 用户、Business 最多 20，Enterprise 另有协议。此处不代替用户选择购买档位。
- **不足：** 它是页面模板和组件系统，不是完整 CRM starter；对话、业务路由与状态需要组合。当前示例公开预览不代表源代码免费。视觉是否符合用户品味仍需直接查看，不能仅因品牌或页面数量判定。

### T06 — Tabler

- **来源与许可：** [官方仓库](https://github.com/tabler/tabler) · [MIT](https://github.com/tabler/tabler/blob/dev/LICENSE) · [完整预览](https://preview.tabler.io/)。Bootstrap 5、HTML/CSS、Sass 与 TypeScript 资源；框架无关，不是原生 React 组件应用。
- **维护证据：** 默认 dev 分支 [ede09742feef](https://github.com/tabler/tabler/commit/ede09742feef2dacc96e6193cc25772716521a94)，2026-09-09，svgo 依赖修复。正式选择时还需对应稳定 release，不把 dev 自动作为生产版本。
- **对应 BDHub：** CRM、chat、表格、数据表格、表单、设置、不同导航布局可直接比较；大量页面可以支撑运营工具的完整感。
- **不足：** 如果新系统选择 React/Tailwind，需要适配或替换 Bootstrap DOM 插件与样式，不能将其当成 shadcn 组件直接混用。Demo 自带许多传统管理模块，是否符合现代视觉方向需要用户亲看。

### T07 — Mantine Analytics Dashboard / DesignSparx

- **来源与许可：** [作者仓库](https://github.com/design-sparx/mantine-analytics-dashboard) · [MIT](https://github.com/design-sparx/mantine-analytics-dashboard/blob/dev/LICENSE) · [具体 Dashboard](https://mantine-analytics-dashboard.netlify.app/dashboard/default)。当前 package 实测 Next.js 16.1、React 19、**Mantine 7.14.3**、TypeScript；README/仓库描述对 Mantine 和 React 主版本存在不一致，应按 package 选型。
- **维护证据：** [14279e801a4f](https://github.com/design-sparx/mantine-analytics-dashboard/commit/14279e801a4f74d9cdcce67d9b3830b3c51b0f68)，2026-03-29，合并 Next.js 16.1.7 依赖更新，包版本 3.2.0。
- **对应 BDHub：** 明确提供客户、邮件、Chat、项目、任务、通知、Kanban、表单、表格和多种 Dashboard，并有 mock API；覆盖运营模块比较广。
- **不足：** Mantine 是独立组件体系，与 Tailwind/shadcn 不是一套。Demo 有许多行业模块，需精简；本次 HTTP 读取的数据区包含 `No records` 和加载态，未运行客户端不能判定真实演示交互完整。mock API/演示认证不能直接代表正式身份与业务数据方案。

## T08 — 可额外比较的 Tremor 新版子模板群

这组与 T04 来自同一设计/组件家族，作为不同页面起点额外看，不伪装成新的独立模板厂商。两套都已由官方公开免费、MIT，并不需要沿用老 OSS 分支。

| 子模板 | 可直接看的页面 / 源码 | 当前栈与维护证据 | 对 BDHub 的用途与不足 |
| --- | --- | --- | --- |
| **Insights** | [Transactions](https://insights.tremor.so/transactions) · [源码](https://github.com/tremorlabs/template-insights) | Next.js 14.2 / React 18 / Tailwind 3 / TanStack Table；[c94ef1f37567](https://github.com/tremorlabs/template-insights/commit/c94ef1f3756793d541b01df6b2012828182e58bb)，2025-10-08 | 过滤栏、输入抽屉、用户/审核设置、onboarding，适合高密度机会/商品/事项列表；需要补关系会话。HTTP 200，未截图。 |
| **Overview** | [Agents](https://overview.tremor.so/agents) · [源码](https://github.com/tremorlabs/template-overview) | Next.js 15.1 / React 19 / Tailwind 3；[ee80d622c3d9](https://github.com/tremorlabs/template-overview/commit/ee80d622c3d92f25b5b714a91319fd930dbeae7e)，2025-12-05 | 4 类 Dashboard、retention/workflow/agents 指标与图表，适合经营与 Agent 质量复盘；名称 Agents 不代表已经有实际 AI Agent 后端。HTTP 200，未截图。 |

## 三个优先视觉查看入口

以下仅为**覆盖不同形态的查看顺序**，没有替用户选定方案：

1. [T01 — Shadcn Admin / Chats](https://shadcn-admin.netlify.app/chats)：先确认对话工作台和常规管理台密度能否接受。
2. [T03 — TailAdmin Pro / CRM](https://nextjs-demo.tailadmin.com/crm)：比较模块完整度、指标和列表排版；明确这页属于付费 Pro。
3. [T05 — Untitled UI / Dashboard 01 全屏](https://www.untitledui.com/react/iframe/dashboards/dashboard-01)：直接看模板本身，避免文档导航干扰视觉判断。

若更偏重数据操作，可加看 [T08 Insights / Transactions](https://insights.tremor.so/transactions)。选择时用实际页面比较字体、信息密度、列表/会话/表单的一致性及可读性，不用 GitHub star 数替代质量判断。

## 可核实的官方预览图片 URL

以下来自官方/作者 README 或公开 Demo 内容，仅 HEAD 核实为 200；**没有下载这些图片，也没有把 HEAD 成功视为已亲眼查看**。预览图片可能落后于当前 Demo；不得用历史截图替代实际页面体验。

| 候选 | 准确图片 URL | 来源与范围 |
| --- | --- | --- |
| T01 | [作者 README 预览图](https://raw.githubusercontent.com/satnaing/shadcn-admin/main/public/images/shadcn-admin.png) | 作者 README；HEAD 200 |
| T02 | [作者 README Dashboard 封面](https://raw.githubusercontent.com/Kiranism/next-shadcn-dashboard-starter/main/public/shadcn-dashboard.png) | README 中 `/public/shadcn-dashboard.png` 按仓库根解析；HEAD 200；可先看静态图，但后台仍有登录门槛 |
| T03 | [免费 Next.js 版 banner](https://raw.githubusercontent.com/TailAdmin/free-nextjs-admin-dashboard/main/banner.png) | 免费仓库 README；HEAD 200；**不是 Pro CRM 的专属截图** |
| T05 | [Dashboard 01 官方列表缩略图](https://storage.googleapis.com/untitled_figma_snapshots/XBlNn9d3le5EGq89h93B0W-optimized/1765-460094.webp) | 官方 Dashboard 页面中的 Dashboard 01 图；HEAD 200 |
| T06 | [官方 Tabler preview](https://raw.githubusercontent.com/tabler/tabler/dev/shared/static/tabler-preview.png) | 官方 README；HEAD 200 |
| T07 | [作者 Dashboard screenshot](https://raw.githubusercontent.com/design-sparx/mantine-analytics-dashboard/dev/public/dashboard.png) | 作者 README；HEAD 200 |

T04 / T08 的 README 没有直接截图；已从官方 [templates 页面](https://blocks.tremor.so/templates) 出现的 Next.js image `url` 参数还原下列原始公开资源，并逐个 HEAD 核实为 200，没有下载或观看图片：

| 候选 | 官方预览图片 | 用途 |
| --- | --- | --- |
| T04 Dashboard | [charts_2.webp](https://blocks.tremor.so/templates/dashboard/charts_2.webp) · [table.webp](https://blocks.tremor.so/templates/dashboard/table.webp) | 图表/数据表格预览 |
| T08 Insights | [onboarding.webp](https://blocks.tremor.so/templates/insights/onboarding.webp) · [filterbar.webp](https://blocks.tremor.so/templates/insights/filterbar.webp) | 引导/筛选栏预览 |
| T08 Overview | [home.webp](https://blocks.tremor.so/templates/overview/home.webp) · [heatmap.webp](https://blocks.tremor.so/templates/overview/heatmap.webp) | 总览/热力图预览 |


## 主流程补充的现场查看

2026-09-11通过Chrome实际打开并观察：T01的/chats（会话列表与未选择会话的空态）、T05的Dashboard 01全屏页（布局、图表与内容模块）、T03的Pro CRM（指标、图表、导航与列表）。这些只是指定页面的局部视觉检查，不代表全部交互、响应式或源代码验收；未登录T02或购买任何模板。选择目录采用作者官方静态图并单独注明来源，不冒称这些图片为本次截图。
