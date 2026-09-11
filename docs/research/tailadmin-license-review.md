# TailAdmin Next.js 许可核对

核对日期：**2026-09-11**。使用者已选择 T03 TailAdmin Next.js 作为界面方向，**免费版或 Pro 源码来源、是否已有许可、是否购买及购买档位仍未确认**。本轮实际读取官方 license/pricing 和免费 GitHub LICENSE/README，主任务也独立核对相同官方条款。未购买、登录、抓取私有源码或执行下载代码；此文不授权购买。

## 1. 当前 Next.js 单框架 Pro 价格

| 档位 | 当前页面展示价 | 开发席位 | 项目数 | 邮件支持 | Figma 源文件 | SaaS end product |
|---|---|---:|---:|---|---|---|
| Starter | **$59**，划线价 $99 | 1 | 3 | 6个月 | 不含 | 不适用 |
| Business | **$119**，划线价 $279 | 3 | 10 | 12个月 | 包含 | 不适用 |
| Extended | **$299**，划线价 $599 | 10 | 不限 | 12个月 | 包含 | 适用，附 redistribution license |

三档均为一次付款，包含全部 Pro 组件和永久更新。价格是本次观测，不是未来报价；实际购买时重新核对。上述为 **Next.js 单独版本**，不是多框架 Bundle。仅因选择 Next.js 不需要同时购买其他框架包。

官方来源：

- https://tailadmin.com/pricing
- https://tailadmin.com/license

## 2. Seats 是开发者，不是运营用户

价格 FAQ 的原文：

> “Seats refer to the number of developers permitted to collaborate on the project and distribute and share TailAdmin resources.”

项目数量的原文：

> “The term projects refers to the number of projects/websites/tool/product that can be built or developed using TailAdmin.”

因此当前 PRD 的 **1–2 名运营不能推导出 1–2 个开发席位**。席位判断需确认实际参与开发、访问及共享 TailAdmin 资源的人数；不能直接因有两名运营就推荐 Business。官网没有规定按国家数量计算项目，不能把同一系统的 MX、BR、IT 自动当成三个项目。

来源：https://tailadmin.com/pricing

## 3. 免费版 MIT 的可用范围

免费仓库 LICENSE 授予：

> “use, copy, modify, merge, publish, distribute, sublicense, and/or sell”

同时要求：

> “The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.”

免费仓库代码可以修改、用于内部商业工具，并不附上述 Pro 席位及项目数量限制；复制或使用实质部分时必须保留版权和许可声明。README 明确写道 “TailAdmin Next.js Free Version is released under the MIT License.”

该结论针对免费仓库本身；第三方依赖、字体、图片等仍按各自许可判断，不能把仓库 MIT 当成覆盖全部外部资源的统一授权。

原始来源：

- https://raw.githubusercontent.com/TailAdmin/free-nextjs-admin-dashboard/main/LICENSE
- https://raw.githubusercontent.com/TailAdmin/free-nextjs-admin-dashboard/main/README.md
- https://github.com/TailAdmin/free-nextjs-admin-dashboard

## 4. 免费、Pro 与公开 Demo 的边界

README 分别链接 Free 与 Pro Demo，并区分免费组件和 Pro 页面/资源。**能打开公开 Pro Demo，只证明可浏览该演示，不代表已获得 Pro 源码或使用许可。** 免费仓库的 MIT 不延伸覆盖 Pro 私有源码。

可在已选 TailAdmin 风格与免费基础上设计本系统所需页面；不能把 Pro Demo 中存在 Chat、CRM 等页面描述成免费源码已经包含，更不能把演示页当作真实业务后端。需要复用具体 Pro 页面代码时，应先确认合法源码与许可来源。

## 5. SaaS 与分发按官方原文解释

license 页将 Starter、Business 标为 “Not Valid for SaaS End Product”，Extended 标为 “Valid for SaaS End Product with Redistribution License”。价格 FAQ 对 SaaS end product 的描述是：

> “SaaS end product refers to license and permission to redistribute your application code along with the TailAdmin template files as an integral part of your final product which is only available for Extended plan.”

本记录保留这个官方定义，不把它替换成通常所说的所有托管 SaaS，也不推导 Extended 允许任意单独转卖原模板。上述页面没有完整界定“内部工具”、纯托管服务及每种交付方式的所有边界；未来用途若涉及这些差异，需要让厂商按实际交付方式确认。

当前已确定需求是机构内部工具、最终独立替代旧 BDHub；尚无已确认的对外 SaaS 或向第三方分发应用源码需求。因此不能为假设的未来场景提前选购 Extended。

来源：

- https://tailadmin.com/license
- https://tailadmin.com/pricing

## 6. 当前可执行选择与待确认

| 选择 | 适用条件 | 当前状态 |
|---|---|---|
| 免费版＋自行实现所需页面 | 免费基础可满足设计需求，缺少的页面自主实现；保留 MIT 声明 | 可作为无需购买的方案继续评估；不是已经选定的源码来源 |
| Pro Starter | **实际只有1位开发协作者**、本次为内部项目、项目总量在许可内、不需要完整 Figma 源文件，且没有超出该档位的代码分发用途 | 最低档候选，不是购买结论 |
| Pro Business | 需要2–3个开发席位、更多项目，或完整 Figma 源文件 | 依据实际团队与设计交付需求决定 |
| Pro Extended | 明确需要官方所述随最终产品分发应用代码及模板文件，或其他经厂商确认需此档位的用途 | 当前无已确认需求，不先购买 |

继续实施前确认：**免费还是 Pro、实际开发协作者人数、是否需要 Figma、是否已持有相应 Next.js 许可及合法源码。** 若需要购买，由使用者确认具体方案；无需为仅查看 Demo 或继续页面需求设计先购买。

价格、席位和限制是本次官方页面事实；对 BDHub-Agent 的档位适配属于带条件的实施建议，不冒充厂商对本项目的书面许可确认。
