# BDHub-Agent

独立的 Agent 驱动达人经营系统。目标是以 1–2 人运营墨西哥、巴西和意大利的一发、二发及合作服务，逐步迁入必要能力与历史数据，最终可替代旧 BDHub。

打开 **[新匹配工作台](http://127.0.0.1:5198/opportunities?mode=matching)**，或进入[本地合作工作台](http://127.0.0.1:5198/workspace?mode=local)、[界面演示](http://127.0.0.1:5198/overview)。

新增 **[意大利真实数据离线测试](http://127.0.0.1:5198/opportunities?mode=matching&dataset=italy)**：独立导入5商品、48达人、50条同品出单证据，双向回放完整，已准备48份有界评审资料。没有真实发送或模型调用；一发画像与商业方案缺口明确保留。[测试结果与复现](docs/implementation/italy-offline-matching.md)。

**[已有画像自动分析](http://127.0.0.1:5198/opportunities?mode=matching&dataset=italy-profiles)** 已补齐3,978人的多类目与经营数据，自动分析1,543个组合；忽略画像年龄，价格带和形式仅参考，人工反馈可选。全量查询、排序和1,377份资料包已自动验证，未调用业务模型或发送。[当前政策与测试](docs/implementation/existing-profile-auto-analysis.md)。

**[意大利二发工作台](http://127.0.0.1:5198/workspace?mode=second)** 已把48位达人/50条同品关系合并成48条意语稿件，支持冻结、持久模拟队列、暂停和未知核验。[结果台](http://127.0.0.1:5198/results?mode=second)保留47条模拟接收及1条故障用例未知；真实发送0。[范围、来源与复现](docs/implementation/italy-second-pilot.md)。

画像补全已复用MX完整HTTP验证和重放流程：意大利48人实测，31人取得当前画像，26人的22项允许字段全部有值，17人无精确handle；111次业务请求、验证及重放各1次成功。数据留在新项目独立var，未覆盖matching库或真实发送。[补全测试结论与复现](docs/research/italy-profile-completion-test-20260912.md)。

已补齐[稳定身份与改名处理](docs/architecture/creator-identity-and-rename.md)：新handle用于发现，已知OEC直接刷新；3人仅OEC真实请求均成功且Find为0。独立身份库保存31个达人、48条线索（17待解析），重复导入不增人，身份底座85项相关测试通过。[达人库页面](http://127.0.0.1:5198/creators)现已接入，匹配和二发可查看稳定档案；页面按OEC刷新已通过1人真实只读验收，原业务表未改绑。[使用与验证](docs/implementation/creator-identity-ui.md)。

结构化匹配已经可运行：商品找达人、达人找商品、精确同款证据、可解释候选与最多5商品的关系评审包。默认72商品/360达人均为合成事实；1万商品/5万达人的三市场分层离线基准P95约32ms，本轮模型调用和计费token为0。[实现与验收](docs/implementation/structured-matching-v1.md)。

新增本地持久运行切片：示例消息、关系控制、任务、上下文和逐组件回执进入独立 SQLite，Worker 在页面关闭后仍可处理，重新启动进程后从原记录继续。当前采用确定性判断和本地模拟平台，未调用真实模型或 TikTok。[运行范围与恢复验证](docs/implementation/local-runtime-v1.md)。

原型基于真实 **TailAdmin Next.js Free 2.3.0（MIT）**，缺少的业务页面自行实现。七个入口贯通经营目标、二发/一发机会、合作会话、人工决定、MX 历史接续、话术策略与结果查看。原七页和本地运行仍为示例；意大利画像分析同时使用历史资料与身份库增量画像。个性化草稿的模型适配和持久队列已实现，已有真实Flash小样记录，二发现按使用者要求固定模板；真实发送按具体冻结批次启用。旧系统业务和运行保持只读；已授权资料/Skills清理已经结束，此前两套生成 UI 均未采用。

批量扩充达人池已接入 **[达人库](http://127.0.0.1:5198/creators) → 批量添加达人**：支持 handle/@handle/主页链接，预览去重后入队，逐项显示新增、已有及未匹配，支持暂停和页面恢复。当前32位已核验达人、17条待解析线索。[批量入池与实测](docs/implementation/creator-discovery.md)。

新画像现已自动接入 **[画像分析](http://127.0.0.1:5198/opportunities?mode=matching&dataset=italy-profiles)**：原21人更新、新增11人，匹配池3,989位；达人档案可直接“分析适合商品”，旧结果和资料包保留版本。[同步与实测](docs/implementation/registry-profile-matching-sync.md)。

个性化意语邀约草稿已接入 **[画像分析](http://127.0.0.1:5198/opportunities?mode=matching&dataset=italy-profiles) → 整理关系资料包 → 个性化邀约草稿**。当前只支持已关联稳定身份、具有同口径类目适配依据的意大利资料包；可选择风格并填写表达要求。没有已核验 Offer 时只探询合作意向，不承诺佣金、样品或商品卡。独立生成与检查阶段使用同一份冻结事实，保留原文、中文辅助理解、版本及服务商返回的用量；费用按报价估算，未知用量不当零。该个性化入口保留给一发；二发改用固定模板，旧AI实验仅历史可读。[范围、使用与验证记录](docs/implementation/italy-outreach-drafts.md)。

意大利二发已进入 **[固定模板工作台](http://127.0.0.1:5198/workspace?mode=second-live)**：按你提供的墨西哥实用文案准备3套意语模板，已验证31位达人批量填充与去重、模型调用0；旧措辞被否定后已停用，当前v3稿待定稿。真实HTTP鉴权、会话读取和Freegrin商品卡联合核验已通过；卡片逐张确认后才发固定文字，原模拟预演保留。[当前实现与实测边界](docs/implementation/italy-second-system.md)。

## 启动与体验

在 `apps/web` 执行：

```sh
npm ci
npm run build
npm run start
```

本地运行还需在另一个终端、同一目录执行 `npm run runtime:worker`。数据库位于新项目 `var/runtime.sqlite`，不清除历史记录。草稿队列使用另一个独立 Worker：`npm run drafts:worker`；启动 Worker 不会启用模型策略。

开发时使用 `npm run dev`；开发和生产预览共用 `127.0.0.1:5198`，同一时间只启动一种。完整命令与演示流程见 [前端说明](apps/web/README.md)。建议先点击页面中的“体验流程”，依次体验 Sofía 的二发合作、Luca 的人工决定和 Camila 的历史接续。“重置演示”可恢复初始示例。

新增[可复用TikTok接口与Agent能力手册](docs/contracts/tiktok/README.md)：源码路径/字段、三市场只读样本、Agent工具合同与未验证缺口分开登记。

当前实施入口：[意大利二发闭环实施蓝图](docs/implementation/italy-second-closed-loop-blueprint.md)。明确每段输入/接口/状态/完成证据，先统一关系控制和容量驱动供给，再接收信与回复；一发留V2。

已落地[二发本地控制与供给底座B.1](docs/implementation/second-cycle-foundation-v1.md)：31稳定关系/50来源边/33关系商品机会，页面展示资格缺口；持久队列已离线验证，持续采集与真实发送未接通。

已接[意大利实时货盘B.2](docs/implementation/second-cycle-catalog-v1.md)：43活动及完整已选池共6213条方案，2213条满足准备条件，原5款线索商品已有可关联方案；拟分佣尚未建链，Kalodata持续采集尚未接通。

已接[Kalodata持久Worker B.3](docs/implementation/second-cycle-kalodata-v1.md)：分页回执、恢复和待解析库存控制通过离线测试；用户完成插件登录后原2个PID实测成功，新增23条待解析线索；未发送。

已接[线索身份自动交接B.4](docs/implementation/second-cycle-identity-v1.md)：23条新线索核验15成功/8未匹配，新增3个档案；新准备层34关系/36机会，交接与回填自动接入现有Worker，未发送。

已接[商品材料与v4话术B.5](docs/implementation/second-cycle-materials-v1.md)：5商品短名称一次模型生成并缓存，1卡预检匹配、4卡佣金不匹配待准备，旧v3区仅历史参考；未建链/发送。

已完成[4款新卡创建与回查B.6](docs/implementation/second-cycle-card-creation-v1.md)：当前5款均有匹配卡，4创建/0未知/0发送；仍为逐项canary，不开放批量。

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
| 个性化意语草稿 | [使用边界、版本与用量记录](docs/implementation/italy-outreach-drafts.md) |
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

B.7已完成3人只读核对快照及页面，见[second-cycle-review-v1](docs/implementation/second-cycle-review-v1.md)。bru_none远端已有我方5条/回复0/加橱窗通知0，旧库却为0，当前不放行；另2人会话仍待定位。v4执行未接、真实发送0。

B.8最新覆盖B.7：用户确认5条会按月刷新，历史累计5条不证明当前额度不足。已索引IT/ACC6共104会话，新定位conbarakah；两人历史消息均7月底，转为月度额度待核实，非永久排除。见[会话索引与月度额度](docs/implementation/second-cycle-conversation-index-v1.md)。未真实发送。

B.9已接IT已关联会话只读轮询与持久入站事件，见[入站底座](docs/implementation/second-cycle-inbox-v1.md)：11会话/46历史事件（回复1、橱窗1），二轮新增0、待办0、发送0。Worker持续读取但未接完整IM覆盖、原文处理及自动回复；历史不触发回复，新回复等待60秒并停在awaiting_content。

B.10当前接续入口：[真实卡文小循环与调度](docs/implementation/second-cycle-execution-and-scheduler-v1.md)。用户确认的bru_none颈椎枕13%卡＋话术已真实确认，2组件、无重发；delivery-9b6427e0ab4ef600430aecf88a11ca39不得再次投递。新供给调度已启动，自动批量发送尚未开启。当前任务后台续办ID bdhub-agent；按该文继续完成完整闭环，不重复询问已授权的开发步骤。

B.11接续：[正文与人工接续](docs/implementation/second-cycle-service-v1.md)。正文已入本机版本库，历史不触发回复；新咨询规则路由、待办和版本校验交还已接。真实历史AI分类1次/317tokens，仅影子验证；自动回复尚未开启。当前商品上下文/佣金只读工具已接并实测13%，下一步接版本绑定回复意图与执行。
