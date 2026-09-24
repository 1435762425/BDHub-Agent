# BDHub-Agent 项目约定

独立的 Agent 驱动达人经营系统；默认使用简体中文沟通。

## 阅读与维护

- [PROJECT.md](docs/PROJECT.md) 定义产品规则，[TECHNICAL.md](docs/TECHNICAL.md) 定义当前实现；需求或实现变化更新对应文档。
- [当前交接](docs/handoff/codex-takeover-20260919.md) 只保存带时间的运行快照；细节和历史证据按 [文档导航](docs/README.md) 查阅。历史报告不覆盖当前用户指令或主文档。

## 执行边界

- 只修改本项目；旧 `/Users/bjn00003/BDHub/01-BDSystem-V2` 是运行时的只读依赖（账号配置、保存凭据、签名 runtime、DeepSeek key 回退、IT 写入前的旧库检查），不修改其代码、数据库、配置、凭据、服务或任务。
- 本工作目录就是生产：scheduler 按路径拉起子脚本，Web 生产实例在 `apps/web` 运行，工作区改动会被下一次拉起的进程读到。新开发在 `/Users/bjn00003/BDHub/` 下的独立 git worktree 完成并验证后再合入；生产目录已有的未提交改动如何处理由用户决定。
- 保持既有持久授权状态。开发、测试、发布、预检或重启不产生新授权；新执行器首次真实发送必须由用户在页面明确启动。已有 worker、监控和持久任务不因代码编辑自动停止、恢复或重启；改动其合同前核对进程、日志、锁和断点。
- 外部结果未知只核验原意图、原账号和原证据，不换意图/账号重发，不改判成功。
- `var/` 是本机真实状态，不用演示数据替代；只读 SQLite 使用 `mode=ro`，不用初始化路径检查。敏感配置、凭据、身份文件、`var/` 和 `outputs/` 不入 Git。部分受跟踪的 `config/*.json` 会被页面保存改写，提交前区分运营设置与代码改动。

## 实现与交付

- 确定性代码和台账保证身份、金额、资格、额度、去重、暂停、授权及结果状态；模型处理受控语义任务。
- 页面沿 Route → server bridge → 固定 argv CLI → `scripts/lib` 访问状态和平台；React 不操作 SQLite 或实现资格规则（已知例外：`/api/creator-identities` 只读直连 SQLite，待迁回 bridge）。
- 复用现有配置、状态机、锁、lease/fence 与持久意图；不建立平行状态源，不删库或重建任务解决一致性问题。
- 保留无关改动，使用 `codex/` 分支和逻辑提交，不用 reset/checkout 清理他人改动。
- Python 按范围运行 `unittest`；Web 运行相关 Node 测试、build 和 typecheck（tsc 读取 `.next/types`，先 build）。数量恒等式、幂等、unknown、暂停、账号调度和恢复需覆盖正反合同。
- 分开报告代码/测试、本机服务、只读数据、平台写入和业务结果。主文档不累积测试数量、PID 或发布流水；完成后清理无用临时文件。
