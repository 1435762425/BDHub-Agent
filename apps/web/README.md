# BDHub-Agent Web

Next.js 16 / React 19 本机工作台，基于 TailAdmin Next.js Free。当前页面连接 `var/` 中的真实本地台账与 Python CLI；不同模块的外部能力边界不同，不能再把整个应用概括为“纯演示”或“全部生产可写”。

真实发送仍暂停，AI 自动回复仍关闭。`/api/send` 支持预览、冻结、明确 start/stop 和原意图 unknown 核验；只有用户在冻结后点击“确认并开始”才启动发送 worker。

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

这些测试验证页面/API 合同、本地状态机和故障恢复，不替代真实 TikTok/Kalodata 回执或业务验收。

## 主要页面

| 路由 | 当前用途 |
| --- | --- |
| `/it/workspace/send` | A/B 发送池、预览、冻结、明确启动、停止和 unknown 核验 |
| `/it/workspace/inbox` | 收信监控与人工事项 |
| `/it/workspace/history` | 最近 14 日统计和只读日明细 |
| `/it/catalog` | 全托/Campaign、筛分、标准 TapLink、A/B 线索和 OECID |
| `/it/creators` | 稳定达人身份、别名与画像 |
| `/ops/jobs` | 手动作业、材料周期、Kalodata 身份与命名设置 |
| `/ops/accounts?market=it` | 意大利账号分工与能力证据，只读 |
| `/ops/reply-evaluation` | DeepSeek/Jev 影子分类与人工真值；不发送 |

浏览器演示、`/flow-demo`、本地模拟器、旧二发预演/实测和 matching/outreach-drafts 页面已退出生产构建；对应 SQLite 仍作为历史数据保留并继续备份。产品规则见 [项目文档](../../docs/PROJECT.md)，实现结构见 [技术文档](../../docs/TECHNICAL.md)，动态运行状态见 [Codex 接管状态](../../docs/handoff/codex-takeover-20260919.md)。

## 代码边界

- `src/app/api/`：仅本机 API 路由；所有写请求必须限制字段并走服务端 bridge。
- `src/server/`：CLI/SQLite 适配、输入输出校验和错误映射；不在 React 组件中直接操作运行台账。
- `src/features/`：页面、查询 hooks、交互和状态展示；业务资格与幂等不由前端保证。
- `tests/`：Node 合同测试。数量恒等式、未知结果、暂停和输入拒绝需要同时覆盖正向与反向案例。

本机敏感身份与运行数据在仓库根 `config/*.json`（被忽略的本机文件）及 `var/`；不得复制到前端 bundle、日志或 Git。
