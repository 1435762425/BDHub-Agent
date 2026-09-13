# BDHub-Agent

本仓库用于建设独立运行、最终可替代旧 BDHub 的 Agent 驱动达人经营系统。已完成 PRD、架构设计和 `apps/web` 可运行前端原型；UI 已选择 T03 TailAdmin Next.js 免费 MIT 版，缺少的业务页面自行实现。原七页演示保留浏览器状态；`/workspace?mode=local` 已接入新项目独立 SQLite 与持久 Worker，使用确定性 Planner/模拟平台。另已实现 `/opportunities?mode=matching` 的结构化双向召回与评审包，使用独立合成事实库 `var/matching.sqlite`；该合成切片不调用模型或真实平台。

- 当前需求以 `docs/PRD.md` 为准，已确认与未决选择见 `docs/DECISIONS.md`。只读取本次任务需要的资料，不把全部研究报告默认装入上下文。
- 墨西哥、巴西、意大利，先 TikTok IM，后 WhatsApp；一发、二发、回复到合作结果；1–2 人运营。日触达不设固定业务硬上限，真实平台和资源约束仍有效。
- 旧项目 `/Users/bjn00003/BDHub/01-BDSystem-V2` 的业务代码、数据、配置和服务仅供只读调查。本次获准的资料/Skills 清理已完成，范围见 `docs/research/cleanup-register.md`；不扩大为生产修改或继续删除。旧代码用于核对数据、接口与执行语义；一发/二发的选品选人按用户最新要求重新设计，不继承未实用的旧算法。匹配与费用任务先读 `docs/architecture/matching-and-token-budget.md`；不能根据演示界面推导业务规则。
- 不复制旧仓库全集、凭证、浏览器身份、原始会话或过时指令。接口事实按 `docs/contracts/legacy-interface-inventory.md` 的来源和时间核对。
- 重大不确定事项先列事实、选项和影响，再向使用者提问；未回答的项保持未决，不悄悄写成已选方案。
- 原消息、商品条件、拒联、人工控制和执行结果都有事实源。发送结果未知先核验，不盲目重发；内部处理成功不等于外部合作完成。
- 开发 Skills 与产品 Agent 的业务 Skills 分开。只创建有明确场景、输入输出和评估价值的能力，不批量安装或复制通用规则包。
- 测试与变更相称；研究文件检查引用、口径和契约，业务实现覆盖真实状态与恢复条件。UI 遵循已固定 TailAdmin Free 2.3.0 的视觉与基础组件；不沿用已否定生成图。源码来源、改动与许可见 `apps/web/TAILADMIN-SOURCE.json`、`LICENSE` 和 `THIRD_PARTY_NOTICES.md`，不引入 Pro 源码。

匹配实现与基准见 `docs/implementation/structured-matching-v1.md`。

意大利已有数据的离线测试已获授权并完成首轮，见 `docs/implementation/italy-offline-matching.md`。`dataset=italy` 使用独立 `var/matching-italy.sqlite`：5商品/48达人/50同品边，无可执行Offer。OEC与关系控制/拒联未知，不从Kalodata ID或历史handle交集补成已验证身份；只组织离线证据，不发送、不调用模型。真实数据测试与合成库的万级容量基准是不同验收。

`dataset=italy-profiles`的一发实验使用另一个`var/matching-italy-profiles.sqlite`，初始含3,978条7月30日历史OEC画像，不与Kalodata按handle合并，不导入同品销量边。分类保留`CategoryFact`来源与历史/冲突状态；Freegrin分类冲突不自动改标签。人工评审默认未评估，程序或助手不能预填成用户认可；标注不改变执行权。范围见`docs/implementation/italy-first-profile-evaluation.md`。

2026-09-12用户最新分析口径：忽略画像年龄，价格带和内容形式只作参考；依据已有多类目与可比经营数据自动分析，自行完成离线测试，人工标签不是前置条件。原始快照已补齐3,978人的多类目、销量、均播、粉丝和GMV；主动分析与真实执行资格分开。详细规则与全量测试见`docs/implementation/existing-profile-auto-analysis.md`。

本地运行入口：`http://127.0.0.1:5198/workspace?mode=local`；原界面演示：`/overview`。运行和验证命令见 `apps/web/README.md`，底座边界见 `docs/implementation/local-runtime-v1.md`。数据库在新项目 `var/runtime.sqlite`，不进入 Git。Web 与 Worker 必须指向同一文件；不通过删除库或重发解决未知结果。SQLite 是单机验证适配，不锁定正式框架/部署；本地模拟回执不作为生产验收证据。

意大利二发新切片：`/workspace?mode=second`、`/results?mode=second`，使用独立`var/second-italy.sqlite`。48位来源达人/50同品边→48条有来源的意语程序稿→冻结→本地模拟传输；真实发送0。27个历史handle映射仅提示，0个已核验收件OEC；历史Offer与卡不当当前承诺。真实实测范围尚待使用者明确，不能把“实测”默认解释为批量外联。见`docs/implementation/italy-second-pilot.md`；使用同一case不能绕过完成/未知状态，暂停保持，Worker以脚本路径定位数据库。

入口：`README.md`。研究证据保存在 `docs/research/`，不会自动成为生效商业规则。

达人身份以`docs/architecture/creator-identity-and-rename.md`为准：新handle先精确Find，已知OEC直接Profile，不因改名再按旧handle搜索。内部creatorId及关系归属不随名字变，旧handle被其他OEC复用不合并；暂缺名字保留历史核验时间，失败不删达人。新独立身份库为var/creator-identities.sqlite，当前32身份/50线索（17待解析），已在达人库及matching/second-pilot页面展示只读关联，未改绑原业务表；不得把旧库的审计名回填标成当前远端已验证名字。

2026-09-12画像补全测试见`docs/research/italy-profile-completion-test-20260912.md`。用户明确复用MX完整HTTP及现成验证流程后，已补齐EU signer/captcha/transport配置并调用client.post，取代早期单步探针“先人工验证”的限制。ACC6固定48人实测：31个当前同OEC画像，26人22项字段全有值，17人无精确handle；111次业务请求、1次验证和重放均成功。身份文件未改、旧库未写、真实发送0。当前画像与未匹配名单在var/italy-profile-live-completion-20260912/，未覆盖matching/second-pilot库；当前handle→OEC不证明历史Kalodata跨来源身份。Find+[2]离线重组31/31与完整结果相同，但尚未独立运行该精简流程。content_groups保留来源分组，不误称视频主题类目；价格带和形式仍只作参考。后续测试沿同账号与现有验证/重放实现，遇最终错误有界停止，不换号冲验证。

2026-09-12达人库与画像刷新已接入`/creators`，见`docs/implementation/creator-identity-ui.md`。独立刷新队列var/creator-profile-refresh.sqlite和`npm run identity:worker`只消费显式IT/OEC刷新任务；同请求幂等、同达人活动任务归并、失去租约不导入、重启先结算已有报告。页面1人实测成功（Find0、Profile3，含验证重放共4业务请求），该轮旧业务表哈希不变；新观察进入排序由后续增量同步负责，不解锁真实发送。

2026-09-12批量handle入池见`docs/implementation/creator-discovery.md`：达人库可预览并提交最多500项IT名单，格式/重复检查不联网；共享identity Worker顺序处理OEC刷新与handle发现。当前真实小样新增1、已有1、未匹配1，重复/无效零请求，待解析不重复堆积；Find已核验但Profile失败保留OEC且标画像未完成。暂停只停止后续领取、失败不盲重跑；新输入不能按历史handle缓存跳过Find。原匹配/二发业务表仍未改绑。

2026-09-12新画像已接入匹配，见`docs/implementation/registry-profile-matching-sync.md`。italy-profiles按注册表rowid增量投影，原21人更新/新增11人，总3,989；原ID/来源/控制/拒联/冲突保留。经营指标取单一最新观察，历史类别单独保留时点，不将旧指标拼进新统计窗口。相同观察时间完整Profile优先于Find，源库代际/水位和索引原子提交；后台有界重算依赖分区的最新查询，旧run/资料包/判断不改。仅该数据集自动同步，Kalodata二发库不改绑。


2026-09-12个性化意语草稿实现见`docs/implementation/italy-outreach-drafts.md`。入口仅为`italy-profiles`的有效资料包，要求稳定意大利身份及同口径类目适配依据；无已核验Offer时只探询意向，不将表达要求变成佣金、样品、品牌或商品卡承诺。独立writer/reviewer使用冻结事实与版本化业务策略，原文、中文辅助、事实和每阶段usage留在`var/outreach-drafts.sqlite`；费用按报价估算，未知不当零，预留另列。`npm run drafts:worker`启动队列服务但不启用模型策略；当前默认关闭且尚未真实调用。页面仅查看/复制，不发送、不采用、不覆盖冻结二发稿；旧版本保留，未确认提交先按原requestId查回已有任务，模型结果未知不自动重试。`/agents`仍为演示配置，不能充当真实草稿策略。


2026-09-13最新二发口径覆盖此前个性化实验：使用者提供的墨西哥实用模板为canonical；其他国家去掉WhatsApp/email，不固化返校季/八月/销量复苏。二发只做固定模板字段填充（31人已实际批量准备、模型0），一发保留个性化。先卡逐张确认后一次文案，样品是页面可用才申请的条件句，非二发筛选硬门槛。`docs/research/mx-second-message-reference.md`区分用户当前原文、历史TREND四分支、近期网站群发；不要用网站纯文字实验替代普通二发业务。当前实现入口`/workspace?mode=second-live`及`docs/implementation/italy-second-system.md`；新机会/模板、真实trial、原模拟库各自保留。

IT已真实只读验证EU info/id/token、I18N IM host的203/608/301和Freegrin card_list+members绑定，库存1355/达人12%为2026-09-13 00:00北京时间快照，不能当作永久报价。wire Campaign 0与成员来源Campaign分开。真发需当前卡复核、固定scope/sender hash/已批准且未过期trial，不能根据鉴权通过自动发信；旧实际试点4970因用户否定v2话术而暂停、未批准、外部尝试0；不得恢复。当前v3待定稿，用已核验的商品级佣金优势作为推广理由，发卡及文字前重查事实；不得自行批准新版或把旧批准转给新正文。`scripts/italy-second-live.py`和独立卡/文字attempt负责顺序、租约、回执、unknown与partial_delivery。旧系统始终只读；新sender FIFO仅协调新系统，另只读检查旧在途/旧gate，不声称跨新旧原子调度。后续只有确认具体批次后才能执行，原MX任务不恢复。

2026-09-13产品经理最新范围：V1优先二发自循环＋TikTok IM收信与AI回复，一发主动推品/匹配留V2。账号管理、货盘提取刷新、Kalodata持续PID线索供给、达人更新、升佣链接准备与共享额度均是V1必需；不能只做逐条草稿与试点。二发明确为历史同PID带货达人收到新升佣链接，单PID卡＋固定模板，佣金优势是选品前提。具体分配/期限、复联、500新达人/5条的计数主体与解锁证据、Agent动作权限仍待答复；预算暂不讨论。先读docs/architecture/second-cycle-product-review-20260913.md及PRD最新覆盖段；本次校准不启动真实任务，旧被否定试点保持暂停。


2026-09-13第三轮用户确认覆盖前述未决口径：配额按机构×市场，与登录账号无关；新达人我方最多5条，卡占1条；回复或加橱窗后解除这两类新联系限制，重置窗口未知。Campaign剩余>45天、库存>100是硬条件，评分>4是软偏好；佣金沿用旧分配思路。货盘刷新、加入活动、选入、规则建链/更新可日常自动化，周期链接清理纳入设计；本轮未执行平台写入。线索按实际容量分批PID动态补充，不穷举全部。加橱窗标积极、明确拒绝打扰排除营销。真实问题与工具边界校准见 docs/architecture/third-round-mx-evidence-and-replies-20260913.md（路径相对仓库根）；同达人频次是建议未启用。


2026-09-13第四轮回复业务方向：二发索样/预批准邀请解释复推、不再由机构给新样；用完或损坏引导联系商家询问补样，不承诺补寄。活动失效、LIVE缺链接、索更多目录/退款样品问题转人工。个人卖家佣金更高不全局停用方案，如实解释个人条件；只对外报达人佣金，不暴露机构总额/差额，Boost暂不解释。商品相同需核对身份，不将历史带货当现在必有样品或无证据保证品质。完整规则、助手拟定西语稿、工具约束和待校准项见 docs/architecture/reply-policy-round4-20260913.md；草案未发布，未真实回复。

最新产品规则集中入口：docs/architecture/second-cycle-confirmed-policy.md。已确认未解锁第二轮至少48小时、已解锁主动营销每24小时至多1组；简单致谢/加橱窗不追加催拍。人工接续先确认后系统内待办，飞书后续；首页新触达/回复/加橱窗/GMV；允许认可模板与合格池内自调比例。商品级反馈后换适配商品，全局拒联仍停止。此汇总覆盖以上阶段未决口径；尚未发布为运行规则。

下一段实施按docs/implementation/italy-second-closed-loop-blueprint.md：先共享关系控制、货盘资格与容量驱动供给，后接卡片材料、收信、人工待办及回复。保留现有身份和执行证据，固定来源/ACC6限制需按宿主合同改造，不把试点放大或复活旧暂停批次。

B.1已实现本地second_cycle控制/供给原语与只读面板，见docs/implementation/second-cycle-foundation-v1.md。var/second-cycle.sqlite只用于新准备层；旧试点控制尚未接管、真实采集/发送未接通，不把该层暂停当全系统暂停。新联系容量是输入估计，非真实配额查询。

B.2已接IT当前已加入Campaign/已选池只读分页及拟分佣投影，见docs/implementation/second-cycle-catalog-v1.md。按当前旧workspace已选分佣规则计算，不声称已生效卡佣金。当前6213方案/2213合格为本轮快照；新采集不得把旧Kalodata窗口抵扣当前14天供应。完整全球销售发现与周期/Kalodata Worker仍未接。

B.3 Kalodata Worker实现及当前阻断见docs/implementation/second-cycle-kalodata-v1.md。只读有限任务，14天窗口沿旧版滞后2日。HTTP401认证未过，1 blocked/1 queued，未新增真实线索；不得把Worker测试通过当线上采集成功。会话有效后显式恢复原范围，不自动换账号或改旧Cookie。

最新B.3续测：用户插件登录后原Kalodata作业已成功，2 PID/3请求新增23条待解析线索，2 completed/0 blocked；原401仅历史。新准备库sourceEdges73、稳定关系31，未将Kalodata ID当OEC，未发送。见second-cycle-kalodata-v1.md最新续测。

B.4身份自动交接与回填已接，见docs/implementation/second-cycle-identity-v1.md：新23边15绑定/8未匹配，新增3/已有12；新准备层34关系/36机会。既有身份Worker加载桥接，源边不改写、缺证据不按同名绑定；未匹配释放等待解析容量但保留线索。当前无采集失败/在途，不代表发送已开放。
