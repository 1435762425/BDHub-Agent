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

本地运行入口：`http://127.0.0.1:5198/workspace?mode=local`；原界面演示：`/overview`。运行和验证命令见 `apps/web/README.md`，底座边界见 `docs/implementation/local-runtime-v1.md`。数据库在新项目 `var/runtime.sqlite`，不进入 Git。Web 与 Worker 必须指向同一文件；不通过删除库或重发解决未知结果。SQLite 是单机验证适配，不锁定正式框架/部署；本地模拟回执不作为生产验收证据。

入口：`README.md`。研究证据保存在 `docs/research/`，不会自动成为生效商业规则。
