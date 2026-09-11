# BDHub-Agent

独立的 Agent 驱动达人经营系统。目标是以 1–2 人运营墨西哥、巴西和意大利的一发、二发及合作服务，逐步迁入必要能力与历史数据，最终可替代旧 BDHub。

当前交付为研究与设计基础：旧系统只读，尚未编写生产功能、选择模板、迁移数据或启用真实外发。此前生成的两套 UI 风格均未采用，未带入本仓库。

## 最小阅读入口

| 内容 | 入口 |
| --- | --- |
| 产品需求与完成标准 | [详细 PRD](docs/PRD.md) |
| 已确认条件、待决策问题 | [决策登记](docs/DECISIONS.md) |
| 架构取舍与落地顺序 | [架构方案](docs/architecture/system-design.md) |
| 单 Agent、按需协作、上下文与 Skills | [Agent 运行合同](docs/architecture/agent-runtime.md) |
| Mac 与服务器承载判断 | [部署与容量](docs/architecture/deployment-capacity.md) |
| 必要旧接口与事实来源 | [接口迁入清单](docs/contracts/legacy-interface-inventory.md) |
| 真实 UI 模板选择 | [图文选择目录](design/template-catalog.html) · [模板研究](docs/research/ui-template-options.md) |
| 公开 Agent 案例与反证 | [案例复核](docs/research/agent-cases-review.md) |
| 旧文件和自定义 Skills 整理 | [清理登记](docs/research/cleanup-register.md) |

## 项目状态

- 已确认：独立目录与 Git 仓库；旧 BDHub 不修改；三市场、TikTok IM 首发、后续 WhatsApp；范围内自主经营；1–2 人；无固定业务日触达上限。
- 已完成：旧资料与 Skills 只读审计、必要接口盘点、真实模板及官方 Agent 案例研究。
- 当前 Mac：持续开机运行。服务器迁移不预设为必要条件，按实际平台会话与负载验证决定。
- 已确认架构基线：关系 Agent＋程序调度与执行＋按需专家；长期业务数据自控，模型只取当前必要上下文。
- 未决：UI 模板、具体运行框架及部署切换条件；不得用研究建议冒充选择结果。
- 清理候选尚未删除。业务执行器、账号、商业规则和模型费用范围须在试点启用时明确。

本仓库只保存筛选后的需求、契约和证据索引。旧项目的长篇交接、历史设计图、检查转储和原始业务数据不整包复制。默认先读 PRD 与相关决策，再按任务读取一份架构或接口文件。

## 查看模板目录

本机预览：[模板选择目录](http://127.0.0.1:5196/design/template-catalog.html)。也可直接打开 design/template-catalog.html；图片来自官方公开预览资源，需要网络。页面中的“记下此选项”只保存到本机浏览器，须把生成的选择文字发回对话，不会自动通知或购买。

需要重开静态预览时，在本仓库执行 `python3 -m http.server 5196 --bind 127.0.0.1`。这只服务研究文档与模板目录，不启动 BDHub 后端。
