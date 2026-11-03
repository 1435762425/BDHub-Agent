# 新电脑配置 Kalodata

更新：2026-11-03（Asia/Shanghai）。本教程核对现有项目的扩展登录和 HTTP 读取路径，不启用 TikTok 发送或自动运营。以 macOS、Python 3.13 为例。

## 获取激活配置和运行文件

用户已明确授权将真实激活码、现用登录器、扩展和安装脚本公开到 [BDHub-Agent](https://github.com/1435762425/BDHub-Agent) 的 [setup/kalodata](../../setup/kalodata/README.md)。另一台电脑直接克隆主项目，无需另一个仓库的访问权限：

```bash
git clone https://github.com/1435762425/BDHub-Agent.git
cd BDHub-Agent
```

已有克隆时更新到最新 `main` 即可。`setup/kalodata/` 中包括：

- `config/kalodata-identity.json`：本机保存的激活码和初始测试 PID。
- `runtime/collector/Kalodata登录器独立版/cookie_launcher.py`：现用专属登录器。
- `runtime/collector/product_top50/collect.py`：正式 HTTP provider 使用的 Cookie、代理与请求头适配。
- `runtime/extension/`：原电脑实际加载的扩展源码与资源。
- `runtime/requirements-login.txt`、`runtime/manifest.json`：固定登录依赖、资源范围和 hash。

不复制旧 Cookie、`extension_runtime.json`、`.chrome-profile/` 或 `.venv-mac/`；由新电脑安装环境并正常登录生成身份。当前保存的激活码没有在新电脑完成在线验收。

## 安装到开发仓库

在新电脑的 `BDHub-Agent` 根目录执行：

若该仓库的 `var` 已链接到只读交接数据，先另建开发 worktree 安装；只读数据包继续留在原独立目录。

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python setup/kalodata/install_kalodata.py --project "$PWD"
source .env.kalodata.local
```

已有 `.venv` 时直接安装固定依赖即可。安装器只在指定仓库：

1. 写入被 Git 忽略的 `config/kalodata-identity.json`，权限 0600；不回显激活码。
2. 在 `private-data/config.yaml` 中设置 `kalodata.project_dir`，保留其他已有配置；无配置时从公开模板生成。
3. 生成 `.env.kalodata.local`，设置 `KALODATA_PROJECT_DIR` 与 `KALODATA_EXTENSION_DIR`。
4. 创建缺失的 `private-data/data/research/kalodata/.browser.lock`，保留已有锁文件；准备登录所需的本地 `var/`。

回执 `configured=true` 只证明配置已安装。安装器不登录、不查询平台、不启动 worker。

正式 HTTP provider 读取 YAML 的 `kalodata.project_dir`，登录适配同时支持环境变量。只改 `KALODATA_PROJECT_DIR` 会留下两条路径不一致的问题，因此两处由安装器一起配置。每次在新终端使用前先 `source .env.kalodata.local`，Web 开发进程也应从该终端启动，以便继承扩展路径。

旧教程中的 `/Users/bjn00003/…` 是原电脑路径。新电脑使用安装器输出的路径；扩展路径尤其不能只沿用原登录器里的旧缺省值。

## 安装专属浏览器环境

继续在项目根目录、已加载环境变量的终端执行：

```bash
python3.13 -m venv "$KALODATA_PROJECT_DIR/Kalodata登录器独立版/.venv-mac"
KALODATA_LOGIN_PY="$KALODATA_PROJECT_DIR/Kalodata登录器独立版/.venv-mac/bin/python"
"$KALODATA_LOGIN_PY" -m pip install -r setup/kalodata/runtime/requirements-login.txt
"$KALODATA_LOGIN_PY" -m playwright install chromium
```

登录器使用独立解释器：`playwright==1.62.0`、`curl_cffi==0.16.0`；项目主环境继续遵守 `requirements.lock`，不替换其依赖版本。浏览器和虚拟环境在目标电脑重建，不从原电脑复制。

## 激活、刷新与验收

只读交接数据留在独立的 `readonly-data/var`；以下登录与测试使用新开发仓库自己的可写 `var/`。若安装回执显示 `varIsReadOnlySnapshot=true`，先换到独立开发工作区，再进行本节操作。

初次激活：

```bash
umask 077
.venv/bin/python scripts/kalodata-identity.py activate > var/kalodata-activate-result.json
```

它打开加载指定扩展的专属 Chromium，按保存的激活码正常激活与验证登录。需要页面验证时在该窗口完成；只有正式 provider 验证成功后才自动结束。结果 JSON 保存在本地忽略目录，供本机排查。

已有该新电脑的身份、需要刷新时：

```bash
umask 077
.venv/bin/python scripts/kalodata-identity.py refresh > var/kalodata-refresh-result.json
```

真实读取验收使用项目主环境：

```bash
.venv/bin/python scripts/kalodata-identity.py probe --result-only
```

交接配置带一个从原电脑已完成货盘选出的初始测试 PID；可用 `setup/kalodata/install_kalodata.py --project "$PWD" --canary-pid <19位真实商品ID>` 或 probe 的 `--pid` 指定当前有效商品。测试走正式 A 类达人读取接口和原 14 天窗口，会产生一次真实读取，并保存本地检查结果。`--result-only` 不输出激活配置。

| 返回 | 含义 |
| --- | --- |
| `verdict=ready`、`identityOk=true` | 正式读取通过 |
| `verdict=quota_exhausted`、`identityOk=true` | 身份已认证，但当前额度用尽 |
| `auth_required` | 身份未通过，使用该电脑的原登录流程恢复 |
| `business_rejected` / `unreachable` | 业务回应或连接未通过，按真实 detail 排查 |

代理不是必填条件；`proxyConfigured=false`、扩展绿色提示、Cookie 数量和更新弹窗都不能单独证明身份正常。不要把旧 Cookie 注入扩展新发的身份中。

## 常见问题

| 现象 | 检查 |
| --- | --- |
| 找不到 `setup/kalodata` | 当前克隆是否已更新到最新 `main` |
| `kalodata_runtime_missing` | 专属 `.venv-mac/bin/python` 是否已在正确目录创建 |
| `kalodata_extension_missing` | 已 source 环境文件，`KALODATA_EXTENSION_DIR/manifest.json` 存在，专属 Chromium 安装完整 |
| `kalodata_probe_pid_missing` | 配置或 `--pid` 是否提供 19 位真实商品 ID |
| `FileNotFoundError` / `unreachable` | YAML 的 collector 路径、Cookie 是否由本机登录生成、项目专属 `.browser.lock` 是否存在 |
| `target_path_outside_project` | 目标路径是否链接到项目外；只读交接数据使用独立目录，登录配置安装到另一个可写开发 worktree |
| 浏览器成功但 probe 未通过 | 只按正式 provider 的实际回应判断，不采用另一环境的 checker 或弹窗结果 |

原电脑仍按原持久授权运营。本教程没有重置查询窗口、页码、失败预算或额度，也没有把新电脑接成第二个生产执行器。仅移交配置与源码，不把新电脑登录或读取称为已经验收。
