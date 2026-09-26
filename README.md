# BDHub-Agent

独立的 Agent 驱动达人经营系统。IT、BR、MY、UK 共用货盘、线索、身份、持续发送、收信和服务处理流程，各市场保留独立账号、语言、能力和台账边界；自动 A/B 线索均支持四市场；TapLink 清理、会话页人工发送和身份/画像页面操作仍仅支持 IT（见技术文档）。

实际开关、进程和断点以[当前交接](docs/handoff/current.md)及本机台账为准。打开页面、预检、构建与重启不产生发送授权，也不改变已有开关。

当前采用固定双账号分工：通讯账号负责 SDK 收信及原 HTTP 二发/AI 回复，供给账号负责 Campaign、商品、TapLink 和 OECID。全托支持 IT/UK 首次及每 15 天按类目发现，候选增量累积；A/B 持久队列每 7 天到期重查，发送按 4 A / 1 B 分配机会。具体例外与未完成范围见主文档。

## 当前入口

- [项目文档](docs/PROJECT.md)：唯一产品真相源，定义目标、范围、业务流程、规则和完成标准。
- [技术文档](docs/TECHNICAL.md)：唯一技术真相源，定义架构、模块、数据、接口、运行和验证。
- [当前交接](docs/handoff/current.md)：当前 Git 基线、运行状态、已完成能力、阻塞和下一步。
- [意大利合作工作台](http://127.0.0.1:5198/it/workspace/send)：持续发送控制、逐达人不可变 delivery、明确启动与原意图核验；其他已接入市场使用对应市场路径。
- [意大利货盘与材料](http://127.0.0.1:5198/it/catalog)：全托/Campaign、标准 TapLink、A/B 线索和 OECID 准备。
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

本目录就是生产环境：scheduler 按路径拉起子脚本，Web 生产实例在 `apps/web` 占用 `127.0.0.1:5198`。代码改动在 `/Users/bjn00003/BDHub/` 下的独立 git worktree 中进行和验证（代码按 `ROOT.parent/'01-BDSystem-V2'` 定位旧项目，所以 worktree 要放在同一父目录），合入前再核对进程、锁和断点。

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

文档变更运行 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-docs.py` 与 `git diff --check`。按改动范围选择测试；真实发送、建链、选入和外部结果必须另行以持久意图和平台回执验收，不能由离线测试替代。

## 本地配置与状态

`var/`、`outputs/`、Kalodata 激活码、Campaign 联系邮箱和凭据均不进入 Git。本机敏感配置从样例复制：

```bash
cp config/kalodata-identity.example.json config/kalodata-identity.json
cp config/campaign-join.example.json config/campaign-join.json
chmod 600 config/kalodata-identity.json config/campaign-join.json
```

缺少 `var/` 中的真实 SQLite 与证据文件时，只能进行离线开发；不得用演示数据冒充当前业务状态。

## GitHub 与恢复

代码仓库：[1435762425/BDHub-Agent](https://github.com/1435762425/BDHub-Agent)（私有），远端默认分支为 `main`；本机生产检出分支为 `codex/v1-runtime-alignment`。开发使用独立 `codex/` worktree，验证后合入，再显式推送正式分支到远端 `main`，不推送全部实验分支。

SDK 切换前代码标签为 `pre-sdk-rollout-20260926`，已验收的 SDK/HTTP 版本标签为 `sdk-http-four-market-20260926`；标签固定对应发布时的代码，不随后续文档提交移动。GitHub 保存代码和文档，SQLite、身份材料和本机备份仍在 Git 之外。克隆代码不等于恢复可运行生产环境：还依赖真实状态、本机私密配置及只读旧项目运行依赖。回退须保留切换后新消息，按[上线与回退步骤](docs/implementation/sdk-http-rollout-20260926.md)操作。
