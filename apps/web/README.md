# BDHub-Agent Web

Next.js 16 / React 19 本机工作台，基于 TailAdmin Next.js Free。当前页面连接 `var/` 中的真实本地台账与 Python CLI；不同模块的外部能力边界不同，不能再把整个应用概括为“纯演示”或“全部生产可写”。

真实发送仍暂停，AI 自动回复仍关闭。`/api/send` 当前只提供只读预检和保存设置，没有开始发送路由。

## 运行

需要 Node.js `>=22.18.0`，依赖固定在 `package-lock.json`：

```bash
cd /Users/bjn00003/BDHub/BDHub-Agent/apps/web
npm ci
npm run dev
```

开发服务只监听 `127.0.0.1:5198`。生产模式本机预览：

```bash
npm run build
npm run start
```

不要同时启动两个 5198 实例。启动、停止或替换现有实例前，先核对监听 PID 与 cwd。

## 验证

```bash
npm test
npm run typecheck
npm run build
```

按任务还可运行：

```bash
npm run test:runtime
npm run test:matching
```

这些测试验证页面/API 合同、本地状态机和模拟故障，不替代真实 TikTok/Kalodata 回执或业务验收。

## 主要页面

| 路由 | 当前用途 |
| --- | --- |
| `/catalog` | 全托/Campaign 货盘、筛分、入池、备链、线索队列和 OECID |
| `/workspace?mode=second-live` | 二发发送池、收信监控、回复预演、统计和系统状态 |
| `/opportunities` | 匹配研究及一发 V2 入口 |
| `/creators` | 稳定达人身份与画像 |
| `/ops` | 手动作业、定时意向和 Kalodata 身份 |

旧演示路由仍可能存在，但不作为当前 V1 主入口。当前业务链和页面关系见 [文档导航](../../docs/README.md) 与 [Codex 接管状态](../../docs/handoff/codex-takeover-20260919.md)。

## 代码边界

- `src/app/api/`：仅本机 API 路由；所有写请求必须限制字段并走服务端 bridge。
- `src/server/`：CLI/SQLite 适配、输入输出校验和错误映射；不在 React 组件中直接操作运行台账。
- `src/features/`：页面、查询 hooks、交互和状态展示；业务资格与幂等不由前端保证。
- `tests/`：Node 合同测试。数量恒等式、未知结果、暂停和输入拒绝需要同时覆盖正向与反向案例。

本机敏感身份与运行数据在仓库根 `config/*.json`（被忽略的本机文件）及 `var/`；不得复制到前端 bundle、日志或 Git。
