# BDHub-Agent

独立的达人经营系统，以指定数量的二发批次为V1主线：全量准备正式名单与候补，批量准备TapLink，按时发送、跨日续发，阶段性处理回复。旧BDHub保持只读。

## 当前入口

- [产品需求](docs/PRD.md)：最新逐轮确认规则，覆盖旧“发满估算”和滚动备料口径。
- [批次架构与实施状态](docs/architecture/batch-outreach-v2.md)：TapLink在全量准备中的位置、任务对象、阶段、模块进度和验收。
- [本机二发工作台](http://127.0.0.1:5198/workspace?mode=second-live)：指定数量任务卡、可恢复的本地准备检查、当前试验批次与速度曲线。
- [货盘](http://127.0.0.1:5198/catalog)：唯一全托发现源的真实列表、同步/续采和条件核验。
- [达人库](http://127.0.0.1:5198/creators)：稳定OEC和已有画像；旧版经营能力承接仍在进行。

**AI自动回复当前按用户要求暂停，不能由任务完成或重启自动恢复。** 收信与原授权意大利试验批次可继续；整批预检不创建新任务、不建链、不发送。IT主力来源首轮10000个PID已采完并核对，其他市场与全量任务执行器仍未完成验收。

## 开发与运行

前端在apps/web，按[前端运行说明](apps/web/README.md)构建。新项目SQLite与动作证据在var（不入Git）。Python当前复用旧项目.venv运行环境，但不改写旧项目。

最新可追溯实测：[发送吞吐](docs/implementation/second-cycle-throughput-20-v1.md)、[素材解阻与身份提速](docs/implementation/second-cycle-supply-repair-v1.md)。文档数字为历史快照，真实数量以本地数据库和回执为准。

旧研究入口归档在[历史README](docs/archive/README-before-batches-20260913.md)，不作为当前授权或功能状态。TailAdmin Free来源和许可见[来源说明](apps/web/TAILADMIN-SOURCE.json)、[第三方声明](apps/web/THIRD_PARTY_NOTICES.md)。

主力源当前合同与进度：[B.18](docs/implementation/global-opportunity-source-v1.md)。

最新任务创建实现：[任务卡与本地准备协调](docs/implementation/batch-task-cards-v1.md)。当前确认只启动本地检查，整批外部执行继续接入。
