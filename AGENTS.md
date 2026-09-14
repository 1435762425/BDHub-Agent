# BDHub-Agent

独立建设的Agent驱动达人经营系统，最终可替代旧BDHub。默认中文沟通；按任务需要加载资料，保留无关未提交修改。

## 当前需求与入口

- 当前产品基线：[docs/PRD.md](docs/PRD.md)。批次架构、模块完成状态和实施顺序：[docs/architecture/batch-outreach-v2.md](docs/architecture/batch-outreach-v2.md)。这两份覆盖历史滚动备料、自动估算“发满”和同时发送/自动回复口径。
- V1指定数字的二发任务，V2一发。机构×市场独立；IT/MX/BR，先TikTok IM，后WhatsApp。一次准备目标N＋约10%候补；用户可选择立即备料、稍后设置发送时段，缺时段不挡备料但不能通过发送窗口。TapLink材料必须齐备，才能按北京时间窗口分批发送、跨日续发。数量不足默认等待，只有用户明确部分放行才先发已就绪项。
- 去重达人卡＋文字全部回查成功计1；单卡、拒绝、未知另计。准备不占发送额度，候补不增加目标，未知先核验。
- 全托商品已明确取消库存数量门槛：不要求库存字段、不以0/低于100阻塞，不为库存单独采集或先选入；普通/未证实全托商品保留原规则。平台下架/治理/失效与佣金、期限、绑定仍核验。详见[库存政策](docs/implementation/full-managed-no-stock-gate.md)。
- 全托主力货盘只用“高机会商品→仅全球销售商品”自动发现；补齐并验收是上线硬条件。Campaign日全量；已选商品、源发现和TapLink维护分层更新，不能统一每日逐项重抓。
- 默认保留有效且已使用或使用未知的链接；清洗与移除已选商品独立。未知创建/删除不换意图重做。
- 旧版达人库的经营筛选、标签、人工资料、详情和维护应承接；同市场OEC一人一档，机构关系隔离，原始来源进详情。

## 当前手动控制与授权

- **用户已暂停AI自动回复。保持service_reply_config.enabled=0，不启动带--enable的回复Worker；只有用户明确恢复才能打开。** 收信和授权范围内二发仍可继续。暂停证据var/auto-reply-user-pause.json。
- 历史it-bulk-20260913为用户已授权的IT/ACC6、500目标试验批次，可按原权限执行；它不等于新全量任务执行器已完成。旧被否定试点、已确认组件不重发，不从研究结果扩展其他市场发送。
- 单市场机构新联系500为用户业务口径，不按登录账号倍增。真实重置和错误码按证据；已解锁与未解锁额度分别处理。单达人5条含卡且按月会刷新，不能永久排除。
- 全局回复暂停优先于未来自动阶段切换。自然语言任务先形成可审阅任务卡，用户确认一次，任务内部正常动作不逐项反复询问。

## 工作区与验证

- 只修改 `/Users/bjn00003/BDHub/BDHub-Agent`。旧 `/Users/bjn00003/BDHub/01-BDSystem-V2` 的代码、数据、配置、服务仅只读参考；不复制凭证/身份文件，不写旧库，不恢复旧任务。
- UI基于TailAdmin Next.js Free MIT，沿现有设计系统，不引入Pro。工作台本机5198；独立数据在var。真实状态看数据库、原始回执及进程，不用演示值。
- 模型负责语义与有事实的关系决策；程序负责身份、规则、额度、去重、阶段、暂停和外部结果。不为大量二发逐达人生成文案，只按商品缓存短名＋固定模板。
- 未接平台的任务准备判定/本地测试不授予发送许可。失败或结果未知按持久意图核验；不删数据库、重发或修改成功状态解决问题。
- 改动按范围验证，真实结果与离线测试分开报告。仅更新当前相关文档；临时调试文件按需清理，外部动作证据保留。

## 按需资料

- 具体回复、关系与样品口径：[docs/architecture/second-cycle-confirmed-policy.md](docs/architecture/second-cycle-confirmed-policy.md)，与最新PRD冲突时以PRD和用户当前指令为准。
- 稳定身份：[docs/architecture/creator-identity-and-rename.md](docs/architecture/creator-identity-and-rename.md)。TikTok接口：[docs/contracts/tiktok/README.md](docs/contracts/tiktok/README.md)。旧能力：[docs/contracts/legacy-interface-inventory.md](docs/contracts/legacy-interface-inventory.md)。
- 主力全托来源：[B.18](docs/implementation/global-opportunity-source-v1.md)；`/catalog`已接IT首轮10000 PID。来源查询完成不代表已选入/可发送，当前活动ID需详情核实；其他市场与完整任务联动待验收。
- 新全量准备基础与UI预检：[B.17](docs/implementation/batch-preparation-foundation-v2.md)。预检始终不授权发送，不创建新任务。最新[任务级整批准备](docs/implementation/batch-task-preparation-v2.md)已接 Kalodata、身份、商品短名和已选/活动商品的 TapLink 准备；新卡范围 full_preparation_no_messages，不含消息发送/回复/清洗/未选商品选入。旧 local_preparation_only 卡不升级。未选全托自动选入与新任务发送仍待接通。
- 最新真实发送与素材记录：[B.15](docs/implementation/second-cycle-throughput-20-v1.md)、[B.16](docs/implementation/second-cycle-supply-repair-v1.md)。数字为历史快照。
- 早期阶段入口归档：[docs/implementation/stage-index-before-batches-20260913.md](docs/implementation/stage-index-before-batches-20260913.md)。仅在追溯时读，不从历史“不真实发送/允许回复”等描述推导当前权限。

当前首个正式任务：`batch-19e1044b23d017f1f553a2ecac0d`，IT，1000＋100，用户“现在开始”授权立即准备。运行记录见[立即准备](docs/implementation/batch-1000-immediate-start.md)，不要重复创建同一批。

身份批次提速已启用：`creator-profile-refresh.py worker --interval 1 --cohort-size 20`，最多20人共享会话、3条同账号HTTP通道、总3 QPS。需要同时加载新准备/身份模块；旧单活跃索引已换为受事务控制的同拥有者分组。见[提速实测](docs/implementation/batch-preparation-speed-5x.md)，7.19倍指候选增长，不是全批最终总耗时。

用户已授权的单号/多号短时压测见[测试结果](docs/implementation/identity-single-multi-stress.md)。隔离stress manifest可用5/8/12 QPS，生产默认仍3 QPS；单号短测最高约2.06倍，五号出现超时，不把短测或参考样本吞吐当作长期/新增候选速度。
