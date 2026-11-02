# 另一台电脑 / Claude 开发接手

整理时间：2026-11-02（Asia/Shanghai）。本次同步代码、提交历史、测试和项目文档，供另一台电脑继续开发。生产服务仍在原电脑；本说明没有重新检查业务运行结果。

## 代码基线与阅读顺序

- 仓库：[1435762425/BDHub-Agent](https://github.com/1435762425/BDHub-Agent)，默认分支 `main`。
- 同步前本机代码基线：`2be408450e244db03298866a089c63ad2d1d202b`；远端原基线：`45a3b13b1858b242d3ca6204ba6b04796506220d`。本次包含其后 172 个已有提交和本开发接手说明。
- [CLAUDE.md](../../CLAUDE.md) 是兼容入口，通过 `@AGENTS.md` 引用[项目约定](../../AGENTS.md)；不维护第二套规则。
- 先读 [PROJECT.md](../PROJECT.md) 和 [TECHNICAL.md](../TECHNICAL.md)，再读[发送量排查与恢复](send-volume-20261026.md)、[运行故障修复](runtime-health-20261025.md)和[当前交接](current.md)。交接按各段日期理解，不能只看文件名或把旧快照当成实时状态。
- 后续开发范围以用户交代的任务为准；已知未完成边界见 [TECHNICAL §6](../TECHNICAL.md#6-尚未完成的合同与验证边界)。历史方案不是待办清单，也不产生平台写入授权。

最后一个代码提交优化 `delivery_diagnostics.py` 的投递健康汇总，避免重复聚合。此前的发送量修复、巴西一发、样品流程、代理继承与候选去重均已在历史中；其验证范围与生产证据位置见上述日期化交接。

## 克隆与建立开发分支

以下以 macOS 为例。路径使用当前电脑的目录，无须复用原电脑的用户名。

```bash
git clone https://github.com/1435762425/BDHub-Agent.git
cd BDHub-Agent
git rev-parse HEAD
git switch -c codex/your-topic
```

新电脑上的克隆用于开发。原生产电脑的 `/Users/bjn00003/BDHub-Agent` 是正在使用的工作目录；在原电脑开发须按 AGENTS 建立同级独立 worktree。不要将本地未验证改动直接写进生产目录。

## 环境与离线验证

使用 Python 3.13、Node.js `>=22.18.0` 和 npm。依赖固定在 `requirements.lock` 与 `apps/web/package-lock.json`；无需复制原电脑的虚拟环境或 `node_modules`。

在仓库根目录：

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-docs.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
git diff --check
```

在 Web 目录：

```bash
cd apps/web
npm ci
npm test
npm run build
npm run typecheck
```

按改动范围选测试。`typecheck` 读取 `.next/types`，先 build。这里的验证使用代码与测试 fixture，不需要生产数据库；真实平台读取、发送及业务验收另行处理。

本仓 `vendor/` 已包含运行需要的协议源码与必要 manifest。`scripts/vendor-legacy-bdhub.py --check` 还会核对原上游审阅源码，新电脑没有该源码时不能执行完整重建检查；不要因此把原始 BDHub 项目接成运行依赖。修改 vendor 时按 [vendor 说明](../../vendor/README.md)同步清单和补丁，并补齐对应审阅材料。

页面预览可在新电脑 `apps/web` 执行 `npm run dev`，默认只监听 `127.0.0.1:5198`；API 的 Host 校验也绑定该端口。缺真实状态时，相关页面显示缺少或不可确认，不能把这当成生产业务异常，不能用演示数据替换生产状态。

## GitHub 不包含的内容

| 资源 | 新电脑上的处理 |
| --- | --- |
| `var/` 的真实 SQLite、消息、原请求、断点、授权、锁及证据 | 不随代码上传；仅继续开发时不需要。若要迁移生产，须按既有备份与恢复合同另行转移和核验 |
| `private-data/` 的账号登记、私有配置、签名运行环境及协议状态 | 不随代码上传；不能用原始 BDHub 项目的账号或配置代替 |
| Cookie、登录身份、Kalodata 激活信息、联系邮箱、模型/SMTP 凭据 | 不随代码上传；按项目配置样例和账号流程在需要时配置 |
| `outputs/`、本机备份、依赖目录、构建产物和 LaunchAgent | 不随代码上传；构建与服务配置按目标电脑重新生成 |

可提交的参数在 `config/`，私有字段的样例为 `*.example.json` 和 `vendor/config.example.yaml`。样例不是有效账号、可用签名资源或授权记录。代码克隆完成不等于生产迁移完成。

原生产目录另有两处既有未提交改动，本次保留原样、没有纳入提交：

- `config/jobs.json`：本机 `inbox_monitor.enabled=true`，Git 中的版本为 `false`；不能用仓库配置覆盖正在运行的设置。
- `docs/handoff/current.md`：2026-10-13 备份优化及暂停研究的可恢复归档记录。归档与恢复索引仍在原电脑的 `BDHub-Agent-backups`，并非 GitHub 附件。

## 给 Claude 的起始说明

可将下面这段交给新电脑的 Claude，并在末尾补充本次具体需求：

```text
你将继续开发 BDHub-Agent。先读取 CLAUDE.md、AGENTS.md、README.md、
docs/handoff/development-20261102.md、docs/PROJECT.md 和 docs/TECHNICAL.md，
记录当前分支与 commit，核对相关代码和日期化交接后再实施当前需求。
使用 codex/ 开发分支，保留无关改动，在本机隔离验证并给出可审核提交。
原电脑仍运行生产；本克隆没有生产数据库、凭据、身份或持久授权。
遵守项目既有身份、额度、去重、暂停和 unknown 合同，未知结果保留原意图、账号和请求。
完成后说明修改、验证结果及是否需要在原电脑部署；不要把离线测试当成平台结果。
```

跨电脑回传使用功能分支和 GitHub PR。合入与生产发布是两个步骤；发布时按 [TECHNICAL §7](../TECHNICAL.md#7-运行与验证)核对原进程、持久控制与断点，不随代码同步启动第二套真实执行器。
