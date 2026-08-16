# Third-Party Notices

> 更新日期：2026-08-16  
> 状态：`v0.1.0` 公开比赛研究版；依赖与第三方权利仍需在后续版本中持续核对

## 1. 适用范围

OwlPath 根目录 `LICENSE` 仅适用于 OwlPath 贡献者有权授权的自有代码、文档和已明确标记的项目自有资产。项目负责人已批准这些自有内容按 MIT 许可用于公开比赛研究版。第三方软件包、网络服务、医学文献、术语库、数据库和模型受各自的许可、隐私政策和使用条款约束。

本文件的目的是记录开发来源和分发边界，不会把第三方权利改成 OwlPath 的 MIT 许可，也不取代正式的法务或机构审查。

## 2. DeepRare 公开研究仓库

- **项目：** DeepRare: An Agentic System for Rare Disease Diagnosis with Traceable Reasoning
- **公开论文：** [arXiv:2506.20430](https://arxiv.org/abs/2506.20430)
- **公开仓库：** <https://github.com/MAGIC-AI4Med/DeepRare>
- **本次固定参考 commit：** [`7897820c2a15a67956f2f73421fb8e57688f5fe5`](https://github.com/MAGIC-AI4Med/DeepRare/tree/7897820c2a15a67956f2f73421fb8e57688f5fe5)
- **上游许可文件：** [CC BY-NC 4.0](https://github.com/MAGIC-AI4Med/DeepRare/blob/7897820c2a15a67956f2f73421fb8e57688f5fe5/LICENSE)
- **权利归属：** 由 DeepRare 仓库及其作者/权利人自行声明；OwlPath 不对其权利归属作额外表示。

### 参考方式

OwlPath 开发者阅读了上述公开 commit，并参考其研究软件的信息组织方式，以及公开论文所描述的“候选生成—候选级证据核对—有限反思—重排”这一高层研究思想。OwlPath 针对感染病原体独立设计了时间边界、临床专家路由、证据账本、Taxonomy 合同、隐私约束和验证方案；没有复制 DeepRare 的实现或性能数字。

### 未纳入 OwlPath 的内容

本 OwlPath 仓库不包含、复制、改编、vendoring 或再分发 DeepRare 的：

- Python/Shell/前后端代码或代码片段；
- Agent 提示词、评价提示词或其他文字表达；
- 架构图、效果图、网页截图、Logo、GIF 或其他视觉资产；
- GitHub/Hugging Face 数据、病例、向量、模型权重、Exomiser 资源或运行结果；
- DeepRare 仓库的 Git 历史、子模块、完整副本或自动下载器。

因此，DeepRare 的 CC BY-NC 4.0 条款不是 OwlPath 自有代码的许可条款。本声明也不表示 DeepRare、MAGIC-AI4Med、其作者或所属机构与 OwlPath 存在合作、赞助、背书、认证或其他官方关联。

详细差异与可宣称边界见 [`docs/13_deeprare_inspiration_and_differences.md`](docs/13_deeprare_inspiration_and_differences.md)。

## 3. 第三方软件依赖

OwlPath 通过 Python 和 JavaScript/TypeScript 包管理器使用第三方库。实际安装版本应以当前 commit 中的依赖声明和锁文件为准，各依赖的许可以其上游包或源代码分发的原始文件为准。

每次发布和依赖更新时应：

1. 从后端和前端锁文件生成实际依赖清单或 SBOM；
2. 检查许可与 MIT 分发、竞赛上传和预期使用方式的兼容性；
3. 对需要保留许可全文、版权声明或 NOTICE 的包附上对应文件；
4. 不把“可通过包管理器安装”误写成“OwlPath 拥有或重新授权该软件”。

## 4. 外部模型、API 与医学知识源

OwlPath 可连接用户自行配置的模型 Provider，并可查询 Europe PMC、NCBI/PubMed/Taxonomy 等外部资源。这些名称仅用于说明兼容性或数据来源，不表示对 OwlPath 的赞助或背书。

- API Key、帐户、计费、配额和使用权由用户与对应 Provider 之间的条款决定。
- 外部返回的摘要、元数据和术语记录仍受原始来源的权利和引用规则约束。
- 仓库不应收录 Provider 密钥、真实患者资料、未授权全文、未发布数据集或受限数据库副本。
- 仅记录链接、标识符或允许的简短摘要，不当然获得重新分发原始内容的权利。

## 5. Lucide interface icons

OwlPath uses `lucide-react 0.468.0` in the web interface. The workflow screenshot under `assets/screenshots/` therefore contains rendered Lucide icon shapes. Those icon portions are not relicensed as OwlPath-owned artwork and remain under the upstream ISC license:

> Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT). All other copyright (c) for Lucide are held by Lucide Contributors 2022.
>
> Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby granted, provided that the above copyright notice and this permission notice appear in all copies.
>
> THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

Upstream package: <https://www.npmjs.com/package/lucide-react/v/0.468.0>

## 6. 贡献者来源要求

每位贡献者必须确保提交内容是：

- 自主创作且有权以 OwlPath 许可分发；或
- 来自允许当前使用与再分发的第三方来源，且已保留所有要求的署名、许可和修改说明。

不得从 DeepRare 或其他项目复制代码、提示词、图像、Logo、数据或文字，再以“参考”、“重构”或“开源”为由加入 OwlPath。对来源或兼容性有疑问时，在合并前暂停，并更新本文件与对应证据。
