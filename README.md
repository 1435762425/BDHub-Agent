# BDHub-Agent

独立的 Agent 驱动达人经营系统。当前 V1 聚焦意大利 TikTok IM 二发闭环：先准备合格商品与 TapLink，再查询正销量达人、补齐稳定身份、进入发送池，最后执行发送回查、收信监控和服务处理。

真实发送目前保持暂停，AI 自动回复保持关闭。页面可展示发送池和只读预检，但不会因此获得真实发送授权。

## 当前入口

- [Codex 接管状态](docs/handoff/codex-takeover-20260919.md)：当前 Git 基线、运行状态、已完成能力、阻塞和下一步。
- [产品需求](docs/PRD.md)：V1 范围、完整业务流程和验收标准。
- [决策登记](docs/DECISIONS.md)：已确认规则与仍待业务选择的问题。
- [文档导航](docs/README.md)：架构、实现证据、研究与归档的阅读顺序。
- [项目约定](AGENTS.md)：开发和真实业务动作的稳定边界。

## 项目结构

```text
apps/web/          Next.js 工作台与 API bridge
scripts/           Python CLI、worker 与运行入口
scripts/lib/       业务规则、台账、队列和平台适配
tests/             Python 合同测试
config/            可提交的业务参数与本机配置样例
docs/              PRD、决策、架构、实现证据和交接
var/               本机真实状态与证据，不进入 Git
```

## 开发与验证

Web 需要 Node.js `>=22.18.0`：

```bash
cd /Users/bjn00003/BDHub/BDHub-Agent/apps/web
npm ci
npm run dev
```

工作台监听 `http://127.0.0.1:5198`。源码验证：

```bash
npm test
npm run typecheck
npm run build
```

Python 暂时复用旧 BDHub 的兼容环境，但只执行本仓库脚本：

```bash
cd /Users/bjn00003/BDHub/BDHub-Agent
PYTHONDONTWRITEBYTECODE=1 \
  /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python \
  -m unittest discover -s tests
```

按改动范围选择测试；真实发送、建链、选入和外部结果必须另行以持久意图和平台回执验收，不能由离线测试替代。

## 本地配置与状态

`var/`、`outputs/`、Kalodata 激活码、Campaign 联系邮箱和凭据均不进入 Git。本机敏感配置从样例复制：

```bash
cp config/kalodata-identity.example.json config/kalodata-identity.json
cp config/campaign-join.example.json config/campaign-join.json
chmod 600 config/kalodata-identity.json config/campaign-join.json
```

缺少 `var/` 中的真实 SQLite 与证据文件时，只能进行离线开发；不得用演示数据冒充当前业务状态。
