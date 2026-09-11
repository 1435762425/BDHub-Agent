# TailAdmin 页面映射与导航建议

更新：2026-09-11。依据：[PRD](../docs/PRD.md)。用户已选择 **T03 TailAdmin Next.js 的模板视觉方向**；Pro 源码获取、购买档位与交付范围仍待确认。本文件只整理页面复用范围和设计建议，不代表模板已安装或业务已实现，不使用此前被否定的生成图。

## 建议的导航分组

以下是本轮基于 PRD 的**导航建议，尚待界面设计验证**。14 个功能视图按任务归组，不建立 14 个一级菜单。

| 入口 | 包含视图 | 日常使用方式 |
| --- | --- | --- |
| **今日经营** | 01 总览；11 具体决定的汇总入口 | 查看已推进、正在等待和真正需要人决定的事情，下钻到具体事项 |
| **经营计划** | 02 目标/策略；09 话术策略 | 设置方向与商业边界，预览代表性消息；按版本调整与暂停 |
| **商品与机会** | 03 货盘/Offer；04 一发；05 二发；06 机会池 | 页内切换来源和准备状态；同人的多商品机会归并处理 |
| **合作工作台** | 07 关系会话；08 链接/样品；11 个案决定 | 以关系为中心处理多个事项；人工接管、交还与结果核实在详情完成 |
| **经营复盘** | 14 效果/费用/质量 | 比较市场、来源、策略版本和实际合作结果 |
| **Agent**（低频） | 12 Agent/Skills；10 知识/案例/历史整理 | 管理能力与版本。MX 仍可行动历史可从合作工作台进入，不要求日常反复访问知识配置 |
| **设置**（低频） | 13 运行/容量/连接/迁入；账号与团队设置 | 维护连接、身份和资源。影响经营的共同故障在今日经营聚合提示，不要求运营持续盯日志 |

市场 MX/BR/IT 是共享范围；日期按具体视图需要展示。页面优先说明事情、依据和下一步，技术细节在详情展开。商品、机会、关系和事项是不同对象，不能因导航合并而混成同一业务状态。

## 14 个功能视图对应

表中的路由均来自 [TailAdmin 官方 Pro Demo](https://nextjs-demo.tailadmin.com/) 导航，相关页面本轮逐个 HTTP 读取为 200。Free 组件名来自[免费仓库](https://github.com/TailAdmin/free-nextjs-admin-dashboard)。**Pro**表示可在公开演示看到该类页面，不表示已获得其源码；**自定义**表示需要实现 PRD 的业务结构、状态或交互。

| 视图 / PRD | TailAdmin 路由或 Free 组件 | 复用边界 | 主要业务交互与不能直接套用的内容 |
| --- | --- | --- | --- |
| **01 今日经营** F01/F10/F11/F13 | `/crm`、`/ai`；Free `EcommerceMetrics`、`StatisticsChart` | Pro 总览布局；Free 指标/图表；业务自定义 | 展示已推进、等待和待决定，下钻关系。销售订单、订阅套餐收入不能替换成未经核验的合作收益。 |
| **02 目标与策略** F01 | `/form-layout`、`/task-list`；Free `Form`、`Select`、`MultiSelect`、`Switch` | Pro 表单/列表；Free 控件；策略自定义 | 自然语言整理草案，核对缺项、影响范围、保存版本、按范围暂停。普通任务和 Save 按钮没有版本冲突或已作承诺保护。 |
| **03 货盘与 Offer** F02 | `/products-list`、`/add-product` | Pro 商品外壳；Offer 自定义 | 按市场、精确 PID、类目、条件、有效期筛选；展开同商品不同方案。库存状态不等于可发送；Publish Product 不作为真实平台发布入口。 |
| **04 一发匹配** F03 | `/data-tables`、`/profile`；Free `UserMetaCard`、`UserInfoCard`、`Badge` | Pro 高级表格；Free 档案；匹配自定义 | 商品找达人、需求找商品，展开证据和画像缺口，统一同人的机会。人事字段不作为达人适配依据，推荐分不是成交概率。 |
| **05 二发持续发现** F04 | `/data-tables`、`/task-list` | Pro 表格/任务外壳；发现自定义 | 查看 PID、统计窗口、游标、已覆盖范围、新增唯一达人率和准备库存。普通进度不代表平台全量，带货记录不代表仍有实物。 |
| **06 机会池与关系协调** F05 | `/task-list`、`/task-kanban` | Pro 列表/看板；身份与归并自定义 | 区分未核身份、准备中、可推进、已有事项；同关系归并多来源。候选不全部变成人工待办，拖入 Completed 不能写成业务成功。 |
| **07 合作工作台** F07/F08/F10 | `/chat`、`/support-ticket-reply` | Pro 会话/详情布局；关系控制自定义 | 展示原文/中文释义、AI/人工消息、证据、商品事项；接管、交还、取消过期未发稿。联系人和邮箱不替代 OEC 身份；消息显示不等于平台接收。 |
| **08 链接与样品事项** F08 | `/support-tickets`、`/support-ticket-reply` | Pro 服务事项外壳；领域状态自定义 | 核实同款、Offer、申请/物流与有效入口，文字/卡片逐组件核验。工单 Solved 不等于样品收到；查询不能隐式批准或建链。 |
| **09 个性化话术** F06 | `/text-generator`、`/form-layout`；Free `TextArea`、`FormInModal` | Pro 生成工作区；Free 输入；生成约束自定义 | 三种模式、三市场预览，依据/正文/释义并排；单稿和策略版本分开。AI 生成器界面及模型名称不证明已有真实模型调用或关系 Agent。 |
| **10 知识与 MX 历史** F09 | `/file-manager`、`/data-tables`、`/faq` | Pro 资料/表格/问答外壳；知识核实自定义 | 保存事实来源、版本、认可案例；历史四类并列整理，当前核实后接续。文件目录不等于知识质量，FAQ 不直接成为政策，历史导入不触发群回。 |
| **11 需要人决定** F10 | `/support-ticket-reply`、`/modals`；Free `Modal`、`FormInModal` | Pro 详情；Free 弹窗；决策控制自定义 | 呈现请求、证据、选项、费用/交付影响及作用范围；决定后重新规划。成功弹窗不代表控制权已持久改变，一次报价批准不扩成通用权限。 |
| **12 Agent 与 Skills** F12 | `/ai-settings`、`/api-keys`、`/form-layout` | Pro 设置外壳；产品能力自定义 | 管理业务指令、工具/模型/场景版本、市场、预算、案例测试、发布/回滚。Memory/Connector 标签不证明后端能力；产品 Skill 不增加权限。 |
| **13 运行、连接与迁入** F11/F14 | `/integrations`、`/task-list`、`/notifications`；Free `NotificationDropdown`、`Alert` | Pro 连接/任务展示；Free 提示；调度迁入自定义 | 查看市场/账号能力、最后成功、积压、等待、未知与预算；核对源水位和唯一执行归属。连接卡不是 TikTok/WhatsApp 实现，Toast 示例不是耐久故障队列。 |
| **14 效果、费用与质量** F13 | `/analytics`、`/ai`；Free `LineChartOne`、`BarChartOne` | Pro 分析布局；Free 图表；指标归因自定义 | 按市场、来源、策略比较覆盖、回复、采用、样品/内容及人工成本，展开分母和证据。网站访客不是有效达人，币种不直接相加，缺归属不编收入。 |

## 源码与许可核对结果

免费源码实际提供基础电商总览、`/form-elements`、`/basic-tables`、`/calendar`、`/profile`、基础图表、认证表单和 UI 元素；CRM、Chat、高级表格、商品管理、任务管理、AI Settings、Integrations 不在免费页面目录中。Pro 内部组件文件名、具体交付包和所购授权范围目前 **unknown**，不凭 Demo 猜测。

已读取的免费组件也明确是起点：[BasicTableOne](https://github.com/TailAdmin/free-nextjs-admin-dashboard/blob/main/src/components/tables/BasicTableOne.tsx) 使用本地 `tableData`；[Form](https://github.com/TailAdmin/free-nextjs-admin-dashboard/blob/main/src/components/form/Form.tsx) 转交 `onSubmit`；[FormInModal](https://github.com/TailAdmin/free-nextjs-admin-dashboard/blob/main/src/components/example/ModalExample/FormInModal.tsx) 和 [UserInfoCard](https://github.com/TailAdmin/free-nextjs-admin-dashboard/blob/main/src/components/user-profile/UserInfoCard.tsx) 的保存演示只是 `console.log`。所以模板存在表格、表单或登录页，不证明已有真实查询、CRUD、身份认证和权限控制。

Free 源码适用 MIT；Pro 页面适用 [TailAdmin 商业许可](https://tailadmin.com/license)。用户已选视觉方向不等于已购买 Pro、选定商业许可档位或获得平台执行权限。实际源码取得后，再核对组件、交互和允许使用范围。

## 本轮证据边界

- 本轮只读获取 PRD、免费仓库目录/相关组件及官方 Pro 页面 HTML；未购买、登录、下载执行源码或修改业务状态。
- 22 个相关 Pro 路由 HTTP 200；`/ai-settings` 无 title，但设置正文可读。HTTP 获取证明路径存在，不证明客户端交互或后台功能工作。
- **本文件作者未做视觉检查。** 主 Agent 的现场视觉记录另行保存，不能用这里的 HTTP 结果代替。原模板的生成器、AI Chat、AI Dashboard 都不构成产品 Agent 实现。
