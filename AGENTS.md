# BDHub-Agent 项目约定

独立的 Agent 驱动达人经营系统；默认使用简体中文沟通。

## 阅读与维护

- [PROJECT.md](docs/PROJECT.md) 定义产品规则，[TECHNICAL.md](docs/TECHNICAL.md) 定义当前实现；需求或实现变化更新对应文档。
- [当前交接](docs/handoff/current.md) 只保存带时间的运行快照；细节和历史证据按 [文档导航](docs/README.md) 查阅。历史报告不覆盖当前用户指令或主文档。

## 执行边界

- 只修改本项目。原始项目 `/Users/bjn00003/BDHub/01-BDSystem-V2` 是独立运行的另一个项目：本项目运行时不读取它的代码、配置、账号或数据（账号登记、私有配置、签名运行环境及内置协议层写出的状态都在本项目 `private-data/`），也不修改它的代码、数据库、配置、凭据、服务或任务。两边账号各自管理，同一 TikTok 账号只归一个项目使用（本项目 acc1/2/4/5/6/8/9/11，原始项目 acc3/7/10）。
- 本工作目录就是生产：scheduler 按路径拉起子脚本，Web 生产实例在 `apps/web` 运行，工作区改动会被下一次拉起的进程读到。本项目在 `/Users/bjn00003/BDHub-Agent`，备份在 `/Users/bjn00003/BDHub-Agent-backups`；新开发在 `/Users/bjn00003/` 下的独立 git worktree（`BDHub-Agent-<主题>`）完成并验证后再合入；生产目录已有的未提交改动如何处理由用户决定。
- 保持既有持久授权状态。开发、测试、发布、预检或重启不产生新授权；新执行器首次真实发送必须由用户在页面明确启动。已有 worker、监控和持久任务不因代码编辑自动停止、恢复或重启；改动其合同前核对进程、日志、锁和断点。
- 外部结果未知只核验原意图、原账号和原证据，不换意图/账号重发，不改判成功。
- `var/` 是本机真实状态，不用演示数据替代；只读 SQLite 使用 `mode=ro`，不用初始化路径检查。敏感配置、凭据、身份文件、`var/` 和 `outputs/` 不入 Git。部分受跟踪的 `config/*.json` 会被页面保存改写，提交前区分运营设置与代码改动。
- 2026-11-03 用户明确授权公开 `setup/kalodata/` 中的 Kalodata 激活配置快照、登录器、扩展和安装脚本；这是上述凭据不入 Git 规则的指定例外。实际运行的 `config/kalodata-identity.json` 仍被忽略，其他账号、凭据、身份与业务数据不在该例外范围内。

## 实现与交付

- 确定性代码和台账保证身份、金额、资格、额度、去重、暂停、授权及结果状态；模型处理受控语义任务。
- 页面沿 Route → server bridge → 固定 argv CLI → `scripts/lib` 访问状态和平台；React 不操作 SQLite 或实现资格规则。达人身份读取也经只读 Node CLI 执行，不在 Web 主进程同步查询 SQLite。
- 复用现有配置、状态机、锁、lease/fence 与持久意图；不建立平行状态源，不删库或重建任务解决一致性问题。
- 保留无关改动，使用 `codex/` 分支和逻辑提交，不用 reset/checkout 清理他人改动。
- Python 按范围运行 `unittest`；Web 运行相关 Node 测试、build 和 typecheck（tsc 读取 `.next/types`，先 build）。数量恒等式、幂等、unknown、暂停、账号调度和恢复需覆盖正反合同。
- 分开报告代码/测试、本机服务、只读数据、平台写入和业务结果。主文档不累积测试数量、PID 或发布流水；完成后清理无用临时文件。
