# macOS 登录启动与恢复

Web 和 scheduler 使用当前用户的 LaunchAgents。配置仅服务本项目；业务开关、停止标记和原任务断点保持，账号维护与各市场 worker 仍由原 scheduler 管理。

## 安装

在生产目录、已有真实数据库和 Web 构建的前提下，先运行 `ops-restart-check.py preflight` 保存基线。通过 `ops/macos/launch_agents.py render` 生成本机配置，不在开发 worktree 安装，不将生成的 plist 提交 Git：

```bash
.venv/bin/python ops/macos/launch_agents.py render \
  --node /opt/homebrew/bin/node \
  --output-dir var/launchagents
```

生成器将当前代理环境写入配置，保证登录启动使用与手工启动相同的网络出口。 LaunchAgents 的 PATH 优先使用本项目 `private-data/tools/bin`，本机移除 Apple 开发工具后可由经校验的项目私有 Git 支持版本核验；私有二进制、包装入口与安装回执不进入 Git。换代理端口、项目位置或 Node 路径时重新生成；配置不包含其他 shell 环境或业务凭据。

将两份 plist 以 0600 权限安装到 `~/Library/LaunchAgents/`，用 `plutil -lint` 验证，再用 `launchctl bootstrap gui/$(id -u) <plist>` 加载。同名 Web 若已由临时 `launchctl submit` 加载，需要先 `launchctl bootout gui/$(id -u)/io.bdhub.agent.web`，会有短暂网页中断；不要同时启动第二个 5198 实例。安装 scheduler-watch 不停止已有 scheduler 或 worker。

## 行为与验证

- Web 登录即启动，异常退出由 launchd 恢复，最短重启间隔 30 秒。
- scheduler-watch 登录及每 60 秒检查一次。已有 scheduler 时保留；真实数据库缺失或 `var/operations-scheduler.stop` 存在时不启动 scheduler；否则调用原 `start_scheduler`。同次检查独立读取 BR 一发持久授权和未完成任务，需要工作且原进程已退出时调用原 `launch_worker`；原进程仍在时不并行拉起。停止检查与启动之间再次读取控制，用户暂停保持。它不改写任何业务控制表，也不创建新的业务意图。
- 用户主动停止 scheduler 后，watch 保留停止标记。恢复使用原页面或 `jobs.py start-scheduler --market <market>`；它是明确启动入口。
- 停用自动恢复：`launchctl bootout gui/$(id -u)/io.bdhub.agent.scheduler-watch`，并移除对应 plist。单独卸载 watch 不会停止当前业务进程。
- 用 `launchctl print` 验证加载与退出码，回读 5198 页面和 API，执行 `ops-restart-check.py postflight` 核对控制表、原请求、数据库与会话。基线为全停时，角色集合增加是恢复的预期变化，须另核对全部应有角色，而非要求角色集合不变。

账号维护逾期时，启动成功与业务恢复是不同状态。等待原维护队列完成，并以新收信记录、会话 ready、平台确认的投递回执验收；历史 unknown、人工事项与未授权能力不由启动工具解除。

## 多市场同时出现网络故障

多个市场在相近时间出现 `im_transport_error`、`ReadTimeout`、`SSLError` 或 SDK 初始化失败时，先核对共享出口，再处理账号。没有平台登录失效回执时，不把网络错误判为账号失效，也不批量重登或换账号。

1. 读取各市场 `im-session-*.json`、收信状态和日志，区分进程存活、SDK ready、HTTP 实际处理进度及会话覆盖；门户 HTTP 200 不能证明收信恢复。
2. 在已有运维授权范围内保存当前代理组选择、验证候选线路后切换；只改需要的组，不清空整机连接。Mihomo 控制 socket 以当前内核进程参数为准，旧 YAML 中的地址可能已过期；凭据仅在内存使用，不写日志或报告。
3. 同时核对实际线路与客户端保存的选择。仅改内核 API 可能未更新 Clash Verge 的订阅选择；通过客户端保存同一线路，再读取配置确认，避免下次启动退回旧线路。
4. 让原 worker 按自身机制恢复，验证所有运营市场的新 ready 时间、HTTP 处理增量，并跨过一次自然续期。真实来信产生时再核对 SDK 信号；没有新来信时不制造消息作为证明。

线路变化不扩大业务授权，不跳过发送窗口、暂停、冷却或原意图核验。无改善则恢复原线路，保留诊断证据；备份盘未连接等物理条件与软件故障分开报告。
