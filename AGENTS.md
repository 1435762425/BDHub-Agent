# BDHub-Agent

本仓库用于建设独立运行、最终可替代旧 BDHub 的 Agent 驱动达人经营系统。已完成 PRD、架构设计和 `apps/web` 可运行前端原型；UI 已选择 T03 TailAdmin Next.js 免费 MIT 版，缺少的业务页面自行实现。原七页演示保留浏览器状态；`/workspace?mode=local` 已接入新项目独立 SQLite 与持久 Worker，使用确定性 Planner/模拟平台。另已实现 `/opportunities?mode=matching` 的结构化双向召回与评审包，使用独立合成事实库 `var/matching.sqlite`；未调用模型或真实平台。

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

`dataset=italy-profiles`的一发实验使用另一个`var/matching-italy-profiles.sqlite`，含3,978条7月30日历史OEC画像，不与Kalodata按handle合并，不导入同品销量边。分类保留`CategoryFact`来源与历史/冲突状态；Freegrin分类冲突不自动改标签。人工评审默认未评估，程序或助手不能预填成用户认可；标注不改变执行权。范围见`docs/implementation/italy-first-profile-evaluation.md`。

2026-09-12用户最新分析口径：忽略画像年龄，价格带和内容形式只作参考；依据已有多类目与可比经营数据自动分析，自行完成离线测试，人工标签不是前置条件。原始快照已补齐3,978人的多类目、销量、均播、粉丝和GMV；主动分析与真实执行资格分开。详细规则与全量测试见`docs/implementation/existing-profile-auto-analysis.md`。

本地运行入口：`http://127.0.0.1:5198/workspace?mode=local`；原界面演示：`/overview`。运行和验证命令见 `apps/web/README.md`，底座边界见 `docs/implementation/local-runtime-v1.md`。数据库在新项目 `var/runtime.sqlite`，不进入 Git。Web 与 Worker 必须指向同一文件；不通过删除库或重发解决未知结果。SQLite 是单机验证适配，不锁定正式框架/部署；本地模拟回执不作为生产验收证据。

意大利二发新切片：`/workspace?mode=second`、`/results?mode=second`，使用独立`var/second-italy.sqlite`。48位来源达人/50同品边→48条有来源的意语程序稿→冻结→本地模拟传输；真实发送0。27个历史handle映射仅提示，0个已核验收件OEC；历史Offer与卡不当当前承诺。真实实测范围尚待使用者明确，不能把“实测”默认解释为批量外联。见`docs/implementation/italy-second-pilot.md`；使用同一case不能绕过完成/未知状态，暂停保持，Worker以脚本路径定位数据库。

入口：`README.md`。研究证据保存在 `docs/research/`，不会自动成为生效商业规则。

达人身份以`docs/architecture/creator-identity-and-rename.md`为准：新handle先精确Find，已知OEC直接Profile，不因改名再按旧handle搜索。内部creatorId及关系归属不随名字变，旧handle被其他OEC复用不合并；暂缺名字保留历史核验时间，失败不删达人。新独立身份库为var/creator-identities.sqlite，当前32身份/50线索（17待解析），已在达人库及matching/second-pilot页面展示只读关联，未改绑原业务表；不得把旧库的审计名回填标成当前远端已验证名字。

2026-09-12画像补全测试见`docs/research/italy-profile-completion-test-20260912.md`。用户明确复用MX完整HTTP及现成验证流程后，已补齐EU signer/captcha/transport配置并调用client.post，取代早期单步探针“先人工验证”的限制。ACC6固定48人实测：31个当前同OEC画像，26人22项字段全有值，17人无精确handle；111次业务请求、1次验证和重放均成功。身份文件未改、旧库未写、真实发送0。当前画像与未匹配名单在var/italy-profile-live-completion-20260912/，未覆盖matching/second-pilot库；当前handle→OEC不证明历史Kalodata跨来源身份。Find+[2]离线重组31/31与完整结果相同，但尚未独立运行该精简流程。content_groups保留来源分组，不误称视频主题类目；价格带和形式仍只作参考。后续测试沿同账号与现有验证/重放实现，遇最终错误有界停止，不换号冲验证。

2026-09-12达人库与画像刷新已接入`/creators`，见`docs/implementation/creator-identity-ui.md`。独立刷新队列var/creator-profile-refresh.sqlite和`npm run identity:worker`只消费显式IT/OEC刷新任务；同请求幂等、同达人活动任务归并、失去租约不导入、重启先结算已有报告。页面1人实测成功（Find0、Profile3，含验证重放共4业务请求），旧业务表哈希不变；新增观察不自动覆盖匹配排序数据或解锁真实发送。

2026-09-12批量handle入池见`docs/implementation/creator-discovery.md`：达人库可预览并提交最多500项IT名单，格式/重复检查不联网；共享identity Worker顺序处理OEC刷新与handle发现。当前真实小样新增1、已有1、未匹配1，重复/无效零请求，待解析不重复堆积；Find已核验但Profile失败保留OEC且标画像未完成。暂停只停止后续领取、失败不盲重跑；新输入不能按历史handle缓存跳过Find。原匹配/二发业务表仍未改绑。
