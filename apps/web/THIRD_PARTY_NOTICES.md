# 第三方来源与许可

核对日期：2026-09-11；2026-09-24 按实际依赖与文件更新。本文件分别记录模板、字体、示例资源和直接依赖；模板的 MIT 许可不自动覆盖其他来源。精确包版本由 `package-lock.json` 固定，依赖自身和所带第三方代码保留各自声明。

## TailAdmin Next.js Free

- 来源：[TailAdmin/free-nextjs-admin-dashboard](https://github.com/TailAdmin/free-nextjs-admin-dashboard/tree/d3526b35fb7e579a4585129fe6eaa47f54ec9a0b)。
- 版本：2.3.0；提交：`d3526b35fb7e579a4585129fe6eaa47f54ec9a0b`。
- 许可：MIT；`Copyright (c) 2023 TailAdmin`。完整原文保存在 [LICENSE](LICENSE)，已与固定提交的归档逐字核对。
- 采用范围：主题 CSS、部分基础 UI、主题管理与切换、SVG 图标及部分配置；示例头像/产品图已移除。逐文件原始校验值与改动记录在 [TAILADMIN-SOURCE.json](TAILADMIN-SOURCE.json)。
- BDHub 的经营目标、机会、关系会话、决定、历史接续、Agent 配置及共享弹层是本项目实现。未导入 TailAdmin Pro 源码；公开 Pro Demo 只曾用于选型研究。

免费 MIT 授权允许使用、修改与商业使用，复制或分发实质部分时保留版权和许可声明。Pro 的购买档位、开发席位与项目数量限制不适用于这份免费仓库代码。

## Outfit 字体

- 版权：`Copyright 2021 The Outfit Project Authors (https://github.com/Outfitio/Outfit-Fonts)`。
- 许可：SIL Open Font License 1.1，全文见 [OFL-Outfit.txt](public/fonts/OFL-Outfit.txt)。
- 当前文件：`public/fonts/outfit-latin-variable.woff2`，Latin 可变字体，权重 100–900；中文由系统字体回退显示。
- 来源：[Google Fonts CSS](https://fonts.googleapis.com/css2?family=Outfit:wght@100..900&display=swap) 与 [WOFF2 文件](https://fonts.gstatic.com/s/outfit/v15/QGYvz_MVcBeNP4NJtEtq.woff2)，同时保存在 [SOURCE.txt](public/fonts/SOURCE.txt)。原型自行托管该字体。

## 示例图片和图标

早期从上述仓库导入的示例头像与产品图已从 `public/images/` 移除，不再随应用分发；导入清单不再列出它们。

`src/icons/` 来自同一免费仓库。搜索图形提取自免费 `src/layout/AppHeader.tsx`；铃铛只修改颜色填充；`public/favicon.svg` 复制免费 `grid.svg`。这些改动均保留上游几何与 MIT 来源记录。

## 直接 npm 依赖

本次核对实际安装包的 `package.json` 和相应许可文件，不从模板 README 推导依赖许可。

| 依赖 | 固定版本 | 许可与版权来源 |
| --- | --- | --- |
| Next.js | 16.3.4 | MIT；`node_modules/next/license.md`；Copyright (c) 2025 Vercel, Inc. |
| React、React DOM | 19.2.0 | MIT；各包 `LICENSE`；Copyright (c) Meta Platforms, Inc. and affiliates. |
| Tailwind CSS、Tailwind PostCSS | 4.1.17 | MIT；各包 `LICENSE`；Copyright (c) Tailwind Labs, Inc. |
| PostCSS | 8.5.23 | MIT；`node_modules/postcss/LICENSE`；Copyright 2013 Andrey Sitnik |
| SVGR webpack | 8.1.0 | MIT；`node_modules/@svgr/webpack/LICENSE`；Copyright 2017 Smooth Code |
| TypeScript | 5.9.3 | Apache-2.0；`node_modules/typescript/LICENSE.txt` |

当前不含图表依赖，此前的 ApexCharts 已移除。重新引入图表库时按实际版本的许可原文核对；曾检查的 `react-apexcharts` 1.8.0 采用 Community/Commercial/OEM 条款，不是 MIT。

开发用类型包及完整间接依赖由锁文件记录；它们随 npm 包携带的许可和通知不由本文件重新授权。后续对外分发构建时一并保留实际包含代码的版权和许可文件。

## MIT 许可正文

下列正文适用于上表标为 MIT 的直接依赖及其对应版权声明；TailAdmin 的完整独立副本另保存在本目录 LICENSE。

```text
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
