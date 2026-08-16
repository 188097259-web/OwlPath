# OwlPath v0.1.0 第三方内容与权利审查

> 状态：阶段性来源与边界审查。SBOM 已生成，OwlPath 自有资产已获项目负责人公开授权；第三方依赖的许可证兼容性仍需持续尽职核对。

## 1. 审查信息

| 项目 | 记录 |
|---|---|
| 审查版本 | `v0.1.0` 公开比赛研究版 |
| 审查日期 | 2026-08-16 |
| 审查依据 | `THIRD_PARTY_NOTICES.md`、`docs/13_deeprare_inspiration_and_differences.md`、依赖声明与锁文件、`assets/README.md`、`benchmarks/FIGURE_PROVENANCE.md` |
| 文件身份 | `v0.1.0`；精确边界由同目录最终 `MANIFEST.sha256` 逐文件绑定 |
| 报告整理 | Codex 自动化协作工具 |
| OwlPath 自有资产权利人批准 | 已确认（2026-08-16） |
| 独立法务审查 | 未执行；本文件不是法律意见 |

## 2. DeepRare 参考边界

OwlPath 文档记录的参考对象为 DeepRare 公开研究仓库，固定参考 commit 为 `7897820c2a15a67956f2f73421fb8e57688f5fe5`，上游仓库记录的许可为 CC BY-NC 4.0。

当前候选记录的参考方式包括研究软件的信息组织，以及公开论文描述的候选生成、候选级证据核对与有限反思这一高层研究思想；OwlPath 的感染病原体实现和验证合同为独立设计。候选仓库未纳入、复制、改编、vendoring 或再分发 DeepRare 的：

- 代码或代码片段；
- Agent/评价提示词或其他文字表达；
- 架构图、效果图、截图、Logo、GIF 或其他视觉资产；
- GitHub/Hugging Face 数据、病例、向量、模型权重、Exomiser 资源或运行结果；
- DeepRare 仓库副本、Git 历史、子模块、压缩包或自动下载器。

因此，当前证据不支持将 OwlPath 称为 DeepRare 的官方 fork、复现、移植或衍生版，也不支持任何合作、赞助、背书或认证表述。

## 3. 软件依赖

- Python 和 JavaScript/TypeScript 包均是第三方依赖，各自受上游许可证和通知要求约束；OwlPath 根目录 MIT 许可不会改变这些权利。
- 前端 `npm audit` 的已知结果为 0 个已报告漏洞，但漏洞扫描不是许可证审查。
- Python 锁文件和干净副本安装环境的 `pip-audit` 均未发现已知漏洞；这不等于许可证审查。
- 已从最终候选依赖声明生成 Python 与前端 CycloneDX SBOM；发布前仍需核对直接和传递依赖的许可证，并附上必要的许可全文、版权声明和 NOTICE。

## 4. 外部服务与医学知识源

候选代码可连接用户自行配置的模型 Provider，并可查询 Europe PMC、NCBI/PubMed/Taxonomy 等外部资源。这些名称仅用于说明兼容性或数据来源，不表示赞助、合作或背书。

本次未调用真实模型 API，也未将外部服务的 raw 返回内容纳入候选仓库。将来的 API Key、帐户、计费、配额、内容权利和隐私条款必须由使用者另行核对。

## 5. 视觉资产与 Benchmark 图

| 资产 | 当前来源记录 | 公开状态 |
|---|---|---|
| OwlPath Logo | 来自 OwlPath 自有前端 favicon 源 | 项目负责人已授权按仓库 MIT 许可用于公开比赛研究版 |
| 工作流界面截图 | 2026-08-16 截取自本地 OwlPath 页面，未包含病例/结果面板；包含 `lucide-react 0.468.0` 渲染图标 | OwlPath 自有编排部分获 MIT 公开授权；Lucide 图标部分保留上游 ISC 许可与版权声明 |
| Benchmark SVG/PNG | 由仓库内聚合计数制图，不含病例级或医生身份数据 | 项目负责人已授权公开；只能标为 50 例内部探索性结果 |

50 例 Benchmark 不是临床验证。当前不包含逐例配对记录，不能独立重算置信区间或显著性，也不应从聚合图推导普遍医学优越性。

## 6. 结论

当前发布树未纳入 DeepRare 代码、数据、资产或归档，且已对主要参考来源和外部服务边界进行文档化。SBOM 和 Python 依赖审计已完成，OwlPath 自有资产已获项目负责人公开授权，截图内 Lucide 图标的 ISC 许可已单列；更广泛的第三方依赖许可尽职核对仍需持续，故结论为：

`NO_DEEPRARE_PAYLOAD_FOUND; OWLPATH_OWNED_ASSETS_AUTHORIZED; THIRD_PARTY_LICENSE_DUE_DILIGENCE_REMAINS`
