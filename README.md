# BDHub-Agent

独立的 Agent 驱动达人经营系统，按已确认规则和持久授权持续处理商品、达人、发送、收信和回复。市场注册表覆盖 IT、BR、MY、UK、DE、FR、TH，能力与台账按市场隔离；实际运营范围以市场配置和[当前交接](docs/handoff/current.md)为准。

实际开关、进程和断点以[当前交接](docs/handoff/current.md)及本机台账为准。打开页面、预检、构建与重启不产生发送授权，也不改变已有开关。

供给主链为 Campaign/全托 → 标准 TapLink → A/B 线索 → 稳定身份 → 发送池；持续发送、收信、回复和账号维护按各自条件运行。巴西一发与样品审批使用独立页面授权。系统自动等待窗口、供给和额度，按原断点恢复；未知写入只核验原意图，规则之外的事项交给人处理。

## 当前入口

- [另一台电脑 / Claude 开发接手](docs/handoff/development-20261102.md)：克隆、环境安装、阅读顺序和生产状态边界。
- [Kalodata 配置教程](docs/setup/kalodata.md)：公开激活配置、登录器/扩展安装与正式读取验收；文件位于 [setup/kalodata](setup/kalodata/README.md)。
- [项目文档](docs/PROJECT.md)：唯一产品真相源，定义目标、范围、业务流程、规则和完成标准。
- [技术文档](docs/TECHNICAL.md)：唯一技术真相源，定义架构、模块、数据、接口、运行和验证。
- [当前交接](docs/handoff/current.md)与[最新运行修复核验](docs/handoff/runtime-health-20261025.md)：带时间的 Git 基线、运行状态、已完成能力、阻塞和下一步。
- [总控制台](http://127.0.0.1:5198/br/console)：跨市场结果、等待和人工事项。
- [巴西合作工作台](http://127.0.0.1:5198/br/workspace/send)、[货盘](http://127.0.0.1:5198/br/catalog)、[一发与样品](http://127.0.0.1:5198/br/first-outreach)：其他市场替换路径中的市场键。
- [文档管理](docs/README.md)：旧 PRD、决策、架构、实现证据、研究与归档的层级。
- [项目约定](AGENTS.md)：Agent 的执行边界和阅读路由。

## 项目结构

```text
apps/web/          Next.js 工作台与 API bridge
scripts/           Python CLI、worker 与运行入口
scripts/lib/       业务规则、台账、队列和平台适配
tests/             Python 合同测试
config/            可提交的业务参数与本机配置样例
docs/              项目/技术主文档、架构、实现证据和交接
var/               本机真实状态与证据，不进入 Git
```

## 开发与验证

本目录 `/Users/bjn00003/BDHub-Agent` 就是生产环境：scheduler 按路径拉起子脚本，Web 生产实例在 `apps/web` 占用 `127.0.0.1:5198`。代码改动在 `/Users/bjn00003/` 下的独立 git worktree（`BDHub-Agent-<主题>`）中进行和验证，合入前核对进程、锁和断点。运行协议来自本项目 `vendor/`，私有配置和运行资源来自 `private-data/`，不依赖原始 BDHub 项目。

Web 需要 Node.js `>=22.18.0`。在 worktree 的 `apps/web` 中验证：

```bash
npm ci
npm test
npm run build
npm run typecheck
```

Python 使用本项目自己的 3.13 虚拟环境；固定依赖见 `requirements.lock`：

```bash
cd <worktree>
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
```

按改动范围选择测试。文档运行 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-docs.py` 与 `git diff --check`；迁移、vendor 与发布检查见[技术文档 §7](docs/TECHNICAL.md#7-运行与验证)。真实发送、建链、选入和合作结果分别用原意图及平台证据验收。

## 本地配置与状态

`setup/kalodata/` 保存用户明确授权公开的 Kalodata 激活配置快照、登录器、扩展和安装脚本，步骤见[配置教程](docs/setup/kalodata.md)。实际运行配置仍写入被 Git 忽略的 `config/kalodata-identity.json`。`var/`、`outputs/`、其他凭据、联系方式和登录身份继续保存在本机；其余本机配置从样例复制：

```bash
cp config/campaign-join.example.json config/campaign-join.json
chmod 600 config/campaign-join.json
```

缺少 `var/` 中的真实 SQLite 与证据文件时，只能进行离线开发；不得用演示数据冒充当前业务状态。

## GitHub 与恢复

代码仓库：[1435762425/BDHub-Agent](https://github.com/1435762425/BDHub-Agent)，远端默认分支为 `main`。开发使用独立 `codex/` worktree，验证后合入；生产分支与发布基线记录在[当前交接](docs/handoff/current.md)。

GitHub 保存代码和文档；恢复生产还需真实 SQLite、证据、`private-data/` 配置及签名资源，账号身份按账号页重建。普通状态备份不含凭据，异机加密备份包含私有配置。回退保留新消息与原请求，历史 SDK 切换标签和步骤见[上线与回退](docs/implementation/sdk-http-rollout-20260926.md)。
