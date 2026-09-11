# 公开 Agent 案例、框架与运行边界研究

核对日期：2026-09-11。状态：研究输入，尚未作框架选型或实施决定。

本报告整理已经实际读取的官方文档、工程文章和固定提交源码，不包含框架安装、运行验收或真实平台操作。文中的“事实”来自所列原始资料；“推论”说明这些事实如何影响本项目；“建议”是可撤销的候选选择。旧蓝图的三个 Agent 角色不是本报告的前提。

## 1. 研究范围与尚未确定的条件

已确定的业务范围是 MX、BR、IT 的 TikTok IM，一发/二发寻找机会、个性化触达、回复、合作推进到结果；1–2 人运营，无固定业务日触达硬上限，后续接入 WhatsApp。设定范围内自动选人、选品、发信和回复，超出商业条件交人。

尚待回答的两个架构条件是：新系统最终替换旧系统还是长期依赖旧系统；运行部署在本机还是服务器。本报告不代为选择。旧 BDHub 作为只读研究来源，新项目独立存在，不由参考仓库或框架文档推导任何生产操作授权。

核心研究结论是：公开证据支持重新审视“固定三个自主 Agent”，同时支持保留持久业务状态、执行约束与结果核验。值得比较的基线是**一个可复用关系 Agent＋代码工作流＋按需专业委派**；supervisor/router/handoff 和托管耐久运行时作为有条件候选，不能先选架构再寻找证明。

## 2. 公开案例：实际证明与不证明的事情

### 2.1 Anthropic commerce-agents

**事实。** 本次 GitHub API 返回的最新提交为 `fd4d59224ab96b43c6dc6888207c67b3bd5a24cf`，提交时间为 2026-08-31 22:48:58 UTC。以下源码引用全部固定在此提交。README 明确公司、商品和人员均为虚构；不真实下单、扣款或改变在线商品；merchant 写入先暂存、由人批准；仓库是未持续维护、不接受贡献的参考实现。[S1]

架构采用一份领域定义连接 Messages API、Claude Agent SDK、Managed Agents；工具调用经过统一执行器。其 “One model owns the conversation” 配合按需 Skill 和有界专业分析，而不是一组机器人轮流接管。高频通用原则放静态提示，单次参数语义放工具说明，少见多步骤流程放 Skill。根本不提供的能力关闭；暂时故障则返回不可用，两者分开。[S2]

**源码边界。** `DelegationContext` 第 20–31 行包含 `backend/config/session/state` 等对象；`_run_delegate` 第 268–309 行把它们交给受信任扩展函数，限制每轮委派次数并校验结构化结果。不给子模型整段对话证明了上下文隔离，不能证明 Python 扩展受到沙箱或独立权限隔离。[S3]

**采用建议。** 借鉴统一执行合同、事实与金额来源、规则分层、有限委派及能力关闭方式。专业任务应该拿窄的只读接口和任务所需证据，不复制通用后端对象。不要直接依赖该仓库；不要照搬演示逐次批准，也不要把其单 owner 选择升级为所有业务必须遵守的定律。

### 2.2 Anthropic 多 Agent Research 产品

**事实。** 2025-06-13 的工程文章报告：Opus 4 主 Agent＋Sonnet 4 子 Agent 在其内部研究评估中比单 Opus 4 提升 90.2%。同文报告，Agent 约使用聊天 4 倍 tokens，多 Agent 约 15 倍；强共享上下文、任务依赖多的领域并非理想适用场景。这是特定研究任务、模型与评估的结果，不是达人运营收益预测。[S4]

其有效做法包括给委派任务明确目标、范围、输出、来源和资源预算；子任务产出可引用工件以减少反复转述；检查最终结果而非固定工具路径。评估方面，团队尝试多个 judges 后，发现一次带完整准则的模型评判更稳定；人工测试仍能发现自动评估漏掉的来源偏差和异常。[S4]

**推论。** 并行调查商品供给、多来源带货证据和长历史问题有参考价值；每封达人回复固定经过多个自主 Agent 没有得到这项研究支持。“多 Agent 更强”必须同时报告成本、任务可拆分程度和评价样本。

### 2.3 “有效 Agent”指导的时间边界

**事实。** 2024-12-19 的 Building effective agents 区分固定代码路径的 workflows 与模型动态选择步骤的 agents，建议从最简单有效方案起步，只有收益抵偿成本和延迟时才加复杂度。当前页面另提示，2024 年之后工具版图已改变，并引向新的托管 Agent 路径。[S5]

**推论。** 这篇文章支持以测量驱动复杂度，不支持“永远不用框架”或“托管运行时一定不合适”。老文章中的选型建议需要结合当前接口重新检查。

## 3. 架构候选对比

### 3.1 一个关系 Agent＋代码工作流＋按需委派

**事实。** Pydantic AI 官方将 Agent 定义为指令、工具、结构化输出、依赖与模型设置的容器，并明确支持像 FastAPI app 一样复用一个 Agent 实例。[S6] commerce-agents 也展示统一外层会话与受限分析委派的组合。[S2]

**适用推论。** 当多数消息属于同一达人、少量商品与已知业务流程，步骤通常是查事实后决定下一步，单 Agent 是合理基线。机会召回、排序、批次调度可以由代码和批量模型任务完成；不必都定义成自主 Agent。

**采用条件。** 对照样本证明一个提示与工具集合仍易理解、身份和商业规则不混淆、延迟与成本可接受。若为维持单 Agent 被迫塞入大量互斥政策，或者某专业分支长期出现独立失败，再拆分。

### 3.2 supervisor、router 与 handoff

**事实。** OpenAI 当前官方文档区分两种职责：`agents as tools` 由 manager 保留最终回复责任，把 specialist 当有界能力；`handoff` 转移当前分支的对话控制，后续可通过 `last_agent` 接续。官方明确建议尽量从一个 Agent 开始，只有能力隔离、政策隔离、提示清晰度或轨迹可理解性有实质改善才增加 specialist。[S7][S8]

**适用推论。** supervisor 适合需要动态拆分、汇总的复杂分析；router 适合分类稳定且确有不同工具/政策的分支；handoff 适合专家真正负责下一段协商。只是使用不同语言，或同一句话同时问样品与佣金，并不自动构成拆分理由。

**不采用的默认形式。** 不为每轮固定增加分类 Agent、选品 Agent、文案 Agent、审核 Agent。它会引入多份上下文、更多 traces、重复工具读取和错误传播，必须用业务效果证明价值。

### 3.3 持久运行候选

| 候选 | 原始文档支持的能力 | 适用条件与代价 |
|---|---|---|
| 直接模型 API＋应用状态 | 应用控制循环、工具和状态 | 流程短且稳定时透明；但租约、恢复、版本和诊断自建可能成为主要成本 |
| Pydantic AI＋外部持久层 | 类型化依赖/输出与模型工具编排，可接多种耐久后端 | Python 领域接口易隔离测试；一次 `run()` 本身不能代表跨天可靠经营 |
| OpenAI Agents SDK | manager/handoff、会话历史、暂停状态与 tracing | 若这些能力减少实际接入工作可采用；业务事项、频次与外部动作核验仍由应用负责 |
| LangGraph | checkpoint、interrupt、显式图和跨线程 store | 复杂分支、人审、状态检查和回放较多时有价值；简单流程图形化可能增加维护 |
| Temporal＋模型框架 | durable workflow、Activity、长期等待、重试与恢复 | 跨天任务和部署恢复是主要痛点时比较；需承担服务运行、确定性约束与版本管理 |
| 托管 Agent runtime | 服务方运行循环与保存会话，应用提供业务工具 | 可能降低小团队运行负担；取决于连接器、数据边界、费用、事件语义和账户可用性 |

这些并非全是互斥选项。Pydantic AI 可以与 Temporal 组合；OpenAI SDK 也不能替代全部业务状态设计。框架选择要根据需要它替代的工作量，而非名字、星数或旧项目依赖决定。

## 4. 2026 年文档新能力与可用性限制

**已读取事实。** OpenAI 当前官方 Agents 入口区分 Agents API、Agents SDK 和 Responses API。Agents API 使用托管 Codex harness；架构文档允许 `environment.type=none`，让应用处理 function tools，不要求客服 Agent 拥有 shell 或文件环境。[S9]

**未建立的结论。** 本次没有验证账户是否获准使用、具体地区/供应商组合、服务等级、费用、功能稳定等级、断线/回调补回范围，亦未做 TikTok 连接器实验。官方页面存在不能当作本项目生产可靠性证明。此候选不能直接取代本地执行事实、关系身份与商业规则。

**版本变化事实。** 当前 Pydantic AI 文档支持多个持久执行后端；Temporal 集成已推荐 `TemporalDurability`，旧 `TemporalAgent` wrapper 标记将在 v3 移除。其模型调用和带 I/O 的工具进入 Activity，协调逻辑在 Workflow 中运行。[S10]

**建议。** 不在架构稿中冻结旧 API 名称。将托管 runtime 与本地持久运行都保留为候选，在部署位置与旧系统关系确定后，按相同恢复/成本合同验证。当前文档未建立的稳定性或可用范围应明确写成未验证。

## 5. 关系 owner 与模型实例分开

**推论。** “一个达人一个 owner”最有价值的含义是稳定业务身份、当前控制权、跨商品事项和联系协调，而不是每个达人一个常驻模型。有限 worker 可复用同一 Agent 定义；新消息、状态变化或约定时间触发一次判断，等待期间不持续调用模型。[S6]

同人的并行查询可以节省时间；让样品和佣金两个 Agent 同时自由外联容易产生相互矛盾的承诺。若专家需要真正接管复杂协商，可以采用 handoff，同时保留业务层关系控制与事项协调；不必把“永远由同一模型回答”做成不可变规则。[S7]

全局供给和平台容量具有跨达人约束，不能交给各关系 Agent 独立决定。是否需要 supervisor 取决于任务分解是否动态；资源分配、并发、联系频次和归属核验仍可由代码执行。关系责任不等于必须新建一套永久运行的 Agent 组织架构。

## 6. 规划、执行、评估与上下文的组织

**建议：按权限和证据隔离，而非只按模型名字隔离。** 规划层查事实并提行动；执行层核验当前身份、商业条件、控制版本和资源；评估层读取独立轨迹。可靠性来自可测试合同，不来自另一个模型笼统说“看起来正确”。

评估先用代码检查目标、金额、状态、重复动作和完成证据，再用模型判断需求理解、表达与遗漏。OpenAI agent evals 推荐先用 trace 定位工具/路由错误，再建立重复数据集和 eval runs。[S11] commerce evals 明确未提供可直接套用的完整 harness，要求评最终参数与状态，为拒绝配应服务正例；除非步骤本身就是业务约束，不把所有正确答案锁成相同工具路径。[S12]

**上下文事实。** Anthropic 2025-09-29 的文章把上下文视为有限注意资源，推荐少量高信息密度输入、按需检索、压缩、结构化笔记和有收益的专业子任务。文章也指出过度压缩可能丢掉后来才重要的细节。[S13]

**对本项目的建议。** 分开组织当前工作集、可检索原证据、持久偏好/承诺、稳定指令。当前集保留新消息、未结事项和最新相关事实；摘要只作可重建索引，不能成为佣金、拒联或承诺的唯一事实源。检索到历史内容时保留时间，不能让过去条件冒充现在事实。

Skill 索引与工具合同保持简洁，具体流程按需加载。市场政策、提示、Skill、工具 schema、模型与评估准则分别版本化；历史回复可贡献风格、正反例和待确认规则，不自动发布为政策。压缩要测重要事实能否找回，不能只测 token 下降。

## 7. 持久执行不等于外部 exactly-once

**LangGraph 的边界。** persistence 区分线程 checkpoint 与跨线程 store；内存 checkpointer 重启会丢失。interrupt 恢复会从节点开头重新执行，调用之前的副作用必须幂等或拆开。[S14]

**Temporal 的边界。** 官方 Activity 文档明确：Activity 完成但 worker 在报告前崩溃，可能再次执行。框架能够最终观察到一次完成，Activity 本身仍可能执行多次；幂等键需要由被调用服务真正执行，不能仅存在本地状态。[S15]

**推论。** 任一候选都需要业务行动标识、提交前持久记录、外部回执、未知结果核验和不可盲重试的动作类别。如果 TikTok 没有对应幂等或可靠结果查询，任何框架也不能凭恢复能力消除该边界。反过来，已有这些业务规则不意味着必须继续自建所有队列与恢复功能。

## 8. 应当撤销旧三角色假设的条件

1. 单 Agent＋检索已经达到目标质量时，机会分析采用批处理任务，取消独立自主循环。
2. 评估主要是确定性状态检查时，使用测试流水线与抽样 judge，取消常驻评估 Agent。
3. 失败源于接口、事实过期或事件丢失时，修数据与执行边界，不增加“反思 Agent”代替调查。
4. 不同业务分支具有独立政策、权限和长期责任时，允许 handoff，不把所有专业能力永远限定为工具。
5. 租约、跨天恢复和运行诊断成为主要研发负担时，比较 Temporal 或托管 runtime，撤销“数据库自建队列一定最简单”的判断。

**建议的比较方法。** 使用同一组 MX/BR/IT 场景，比较单 Agent 基线、按需委派、必要 handoff 以及两类运行时。测完整结果、错误动作、人工分钟、P95 延迟、恢复行为和每事项全成本；不只比生成速度或代码量。多 Agent 需要同时给出质量增益和新增成本，才能说明对 1–2 人团队有实际价值。

## 9. 待决策登记

以下是尚需经营方选择的分歧，不是本报告给出的既定答案。前两项正在等待回答，不重复发起提问。

| 分歧 | 为什么影响架构 | 当前处理 |
|---|---|---|
| 最终替换旧系统，还是长期依赖旧系统 | 决定领域状态、接口归属、迁移与长期维护责任 | 保持两种候选，不把“复用旧代码”当最终架构 |
| 本机还是服务器运行 | 决定全天在线、连接器位置、故障恢复和运维负担 | 保留本机/服务器部署候选；不承诺桌面休眠时仍在线 |
| 是否允许会话与运行状态长期托管供应商侧 | 决定托管 Agent runtime 是否进入最终实施候选 | 先登记数据、费用与控制边界；尚未确认正式数据托管 |
| 首轮比较优先缩短人工时间，还是提高相同人力下新增覆盖 | 决定试点主指标及调度取舍，避免同时优化所有指标却无判断依据 | 两者均保留指标，试点前确定主次，不擅设固定触达量 |

范围内自动协商的具体数值仍由后续商业规则定义；不通过增加 Agent 或选择某框架扩展权限。本报告不直接推荐某一框架为最终答案。

## 10. 原始来源

下列来源内容均在 2026-09-11 实际读取；GitHub 文件通过固定提交的 raw 内容核对。官方动态文档未注明发布日期的，以下只记录访问日期；不存在已验证的本项目稳定性或吞吐结论。

- **S1** Anthropic，[commerce-agents README](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/README.md)；固定提交 2026-08-31。提交信息通过 [GitHub commits API](https://api.github.com/repos/anthropics/commerce-agents/commits?per_page=1)核对。
- **S2** Anthropic，[Commerce agent architecture](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/plugins/commerce-builder/skills/commerce-architecture/SKILL.md)；同一固定提交。
- **S3** Anthropic，[delegation.py](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/commerce-common/commerce_common/delegation.py#L20)、[execution.py](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/commerce-common/commerce_common/execution.py#L268)；同一固定提交，分别核对 20–31、268–309 行。
- **S4** Anthropic，[How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)，2025-06-13。
- **S5** Anthropic，[Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)，2024-12-19；访问时页面包含工具版图已变化的说明。
- **S6** Pydantic AI，[Agents](https://ai.pydantic.dev/agents/)；动态官方文档，访问 2026-09-11。
- **S7** OpenAI，[Orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration)；动态官方文档，访问 2026-09-11。
- **S8** OpenAI，[Results and state](https://developers.openai.com/api/docs/guides/agents/results)；动态官方文档，访问 2026-09-11。
- **S9** OpenAI，[Agents 运行时指引](https://developers.openai.com/api/docs/guides/agents-sdk/)、[Agents API architecture](https://developers.openai.com/api/docs/guides/agents-api/architecture)；前者是本次实际访问的旧 agents-sdk 入口，返回运行时比较及新目录链接，后者另行读取；访问 2026-09-11。
- **S10** Pydantic AI，[Durable Execution](https://ai.pydantic.dev/durable_execution/)、[Temporal](https://ai.pydantic.dev/durable_execution/temporal/)；动态官方文档，访问 2026-09-11。
- **S11** OpenAI，[Evaluate agent workflows](https://developers.openai.com/api/docs/guides/agent-evals/)；动态官方文档，访问 2026-09-11。
- **S12** Anthropic，[Commerce agent evals](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/plugins/commerce-builder/skills/commerce-evals/SKILL.md)；固定提交 2026-08-31。
- **S13** Anthropic，[Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)，2025-09-29。
- **S14** LangGraph，[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)；动态官方文档，访问 2026-09-11。旧 durable-execution 入口本次返回 Persistence 内容，按实际页面引用。
- **S15** Temporal，[Activity Definition](https://docs.temporal.io/activity-definition)；动态官方文档，访问 2026-09-11，重点为 Activity Definition Idempotency 与 Activity retry policy。
