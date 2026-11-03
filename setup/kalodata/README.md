# BDHub-Agent Kalodata 公开配置与运行文件

2026-11-03，用户明确授权将本目录中的 Kalodata 激活码、配置教程、登录器、扩展和安装脚本公开到主项目仓库。克隆主项目即可获取本目录。

- `config/kalodata-identity.json`：当前本机保存的激活码；初始测试 PID 来自已完成货盘，不表示已在新电脑验收。
- `runtime/collector/`：现用登录器和 HTTP 读取器源码，不含 Cookie、浏览器身份和虚拟环境。
- `runtime/extension/`：现用扩展源码与资源，不含扩展的用户存储。
- `runtime/requirements-login.txt`：原电脑登录器环境的固定依赖。
- `install_kalodata.py`：向指定的新电脑开发仓库安装配置、生成路径环境文件；不启动登录、查询或业务 worker。
- `runtime/manifest.json`：运行文件范围与 SHA-256。

完整步骤见 [Kalodata 配置教程](../../docs/setup/kalodata.md)。在主项目根目录安装：

```bash
.venv/bin/python setup/kalodata/install_kalodata.py --project "$PWD"
source .env.kalodata.local
```

本目录中的激活配置是移交时的快照；实际运行读取被 Git 忽略的 `config/kalodata-identity.json`。`var/` 的只读数据包仍单独传输。本目录不包含 TikTok 账号、模型密钥、SMTP 密钥或原电脑登录态；不改变原生产任务、窗口和授权。
