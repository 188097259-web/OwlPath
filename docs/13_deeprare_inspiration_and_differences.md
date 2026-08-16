# DeepRare 公开仓库参考边界与 OwlPath 差异说明

> 文档状态：`v0.1.0` 公开比赛研究版
> 核对日期：2026-08-16  
> 用途：说明仓库设计灵感、独立开发边界和可对外宣称的范围

## 1. 本次核对的公开参考

| 项目 | 已核对信息 |
|---|---|
| 公开论文 | [DeepRare: An Agentic System for Rare Disease Diagnosis with Traceable Reasoning（arXiv:2506.20430）](https://arxiv.org/abs/2506.20430) |
| 公开仓库 | [MAGIC-AI4Med/DeepRare](https://github.com/MAGIC-AI4Med/DeepRare) |
| 默认分支 | `main` |
| 固定参考提交 | [`7897820c2a15a67956f2f73421fb8e57688f5fe5`](https://github.com/MAGIC-AI4Med/DeepRare/tree/7897820c2a15a67956f2f73421fb8e57688f5fe5) |
| 提交时间 | 2026-04-14 23:24:06 +08:00 |
| 仓库声明的许可 | [Creative Commons Attribution-NonCommercial 4.0 International（CC BY-NC 4.0）](https://github.com/MAGIC-AI4Med/DeepRare/blob/7897820c2a15a67956f2f73421fb8e57688f5fe5/LICENSE) |

上述 commit 是本文档的固定参考点。仓库后续变化不会自动成为 OwlPath 的实现事实；若要更新比较，必须重新核对并记录新的 commit。

## 2. OwlPath 借鉴了什么

OwlPath 从 DeepRare 的公开论文与固定仓库提交中获得了两类高层启发：研究软件的信息组织方式，以及“先形成候选、再围绕候选检索支持与反证、有限反思后重排”的研究方法。OwlPath 面向感染病原体重新定义了数据边界、Agent 角色、术语体系、证据合同和验证方案，全部实现均为独立开发。

具体参考包括：

1. README 首屏同时说明研究问题、系统概览、论文/数据/演示入口和许可信息。
2. 用架构图、效果图和演示材料降低读者理解成本。
3. 将模型 Provider 适配、外部工具、主流程、输入处理和评价脚本分开。
4. 在仓库中给出系统要求、安装方式、运行入口、复现步骤、引用和致谢。
5. 将大型数据、运行结果和凭据排除在 Git 跟踪之外。
6. 将候选生成与候选验证分成两个阶段，避免一次生成直接等同于最终答案。
7. 使用多来源召回、候选级证据核对与有限反思，作为需要由 OwlPath 自身消融实验验证的研究假设。

这些是公开学术思想与常见研究软件方法的参考，不是对 DeepRare 代码、提示词、图表、数据、结果数字或文字表达的复制。OwlPath 网页不展示 DeepRare 品牌化流程名称或论文性能数字；准确来源与边界集中保留在本文档和 `THIRD_PARTY_NOTICES.md`。

## 3. 明确的独立开发边界

本 OwlPath 仓库：

- **不包含、不复制、不改编** DeepRare 的源代码、提示词、图像、架构图、效果图、Logo、演示 GIF、数据库、数据集、模型权重或运行结果。
- 不将 DeepRare 仓库或其 Hugging Face 数据作为 OwlPath 的 Git 子模块、vendored 依赖、内置资源或隐藏下载项。
- 不使用 DeepRare 的名称、Logo 或视觉素材暗示合作、背书、联合发布或官方关联。
- 不因为阅读过公开仓库就将 OwlPath 标记为 DeepRare 的 fork、port、复现或衍生版本。
- OwlPath 自有代码依本仓库根目录 `LICENSE` 发布；DeepRare 的 CC BY-NC 4.0 不会因“参考了仓库结构”而自动改变 OwlPath 自有代码的许可。

如果未来贡献者希望引入 DeepRare 的任何可受保护内容，必须先停止合并，完成来源、权利、署名、非商业限制和下游分发影响的单独审查，并同步更新 `THIRD_PARTY_NOTICES.md`。

## 4. 从公开研究代码到 OwlPath 的工程升级

| 维度 | DeepRare 指定 commit 中可见的公开形式 | OwlPath 的独立设计/升级 |
|---|---|---|
| 研究目标 | 罕见病候选诊断，主要使用 HPO，可选基因/VCF | 急危重症感染场景中的具体命名病原体 Top-5 研究推演 |
| 执行结构 | 根目录脚本与 Python 函数组成研究流程 | 冻结的多专科 Agent 执行图，包含路由、检索、总诊、确定性验证、审稿和最多一次修订 |
| 输出合同 | 主要输出自由文本，再从 Markdown 名称中提取候选 | Pydantic 模型与本地语义验证约束 `owlpath.result.v3`，要求恰好 5 个唯一、具体病原体 |
| 术语标准化 | 候选疾病向 Orphanet/OMIM 等罕见病资源映射 | 病原体拉丁学名、中英文名称与 NCBI Taxonomy 解析状态分开记录 |
| 证据追踪 | 结果 JSON 保存主要中间输出 | 使用稳定 `source_fragment_id`、证据来源 ID、Agent 角色、模型指纹、尝试次数和版本化 trace；不保存或展示隐藏思维链 |
| Provider 调度 | 有多 Provider 适配，但公开主链仍有 OpenAI mini completion/向量的固定依赖 | 运行开始前冻结可用 Provider，按角色分配，并实施有界重试、故障转移、调用预算和总截止时间 |
| 检索隐私 | 公开研究代码可将 `patient_info` 作为网页检索查询 | 外部文献检索只接收去标识化的综合征、暴露、解剖部位和候选名称，不发送整段病例原文 |
| 病例与运行隔离 | 公开脚本以本地文件保存病例和中间输出 | URL 中 `run_id` 是展示身份的唯一权威来源，后端持久化并防止跨运行/跨标签页串病例 |
| 故障表达 | 多处使用 `print`/`None`/宽泛异常处理 | 技术故障、检索部分、合同问题和开发观察告警使用不同的结构化状态 |
| 测试 | 指定 commit 未包含自动化测试目录或 CI workflow | OwlPath 已有后端单元/集成/合同测试和前端路由/文案/构建测试，并使用 `make test` 统一调用 |
| 全栈实现 | README 描述 FastAPI、Redis、SQL 和微服务，但这些生产 Web 实现未出现在指定公开 commit 中 | OwlPath 公开仓库直接包含 FastAPI/SQLite 后端与 React/TypeScript 前端源码 |
| CI 与容器 | 指定 commit 无 GitHub Actions、Dockerfile 或 Compose | OwlPath 公开仓库已纳入 GitHub Actions、Dockerfile 与 Compose；自动测试已在本地等价环境通过，但本机没有 Docker CLI，尚不能宣称容器冷启动已经验收 |

## 5. 我们没有照搬的公开仓库局限

下列特征是指定 DeepRare 公开 commit 的可复核现状，不是对作者论文或非公开生产系统的评价：

- clean clone 不包含 `database/` 与 `dataset/` 中的运行数据；README 的 Hugging Face 下载步骤只能补齐部分资源，公开代码仍引用多个未随 Git 发布的 `dataset/*.csv`。
- `requirements.txt` 更接近单一环境的全量冻结，包含大量 CUDA 包且缺少平台条件；其中部分固定版本与 README 的 Python 3.8+ 说明不一致。
- 指定 commit 没有提供 tests、CI、Docker、`.env.example`、`SECURITY.md`、`CONTRIBUTING.md`、`CITATION.cff` 或版本化输出 Schema。
- 评价脚本使用 LLM 判断预测排名，并可对无效结果文件直接删除；OwlPath 不使用这种方式替代可审计的标签裁定和确定性指标计算。
- 自由文本解析、`eval()` 解析 CSV 字段、未固定的随机打乱和缺少运行清单不适合 OwlPath 的可追溯目标。

## 6. 不得对外宣称的内容

除非未来有独立、可核查的证据，OwlPath 不得宣称：

1. OwlPath 是 DeepRare 的官方 fork、复现、移植、衍生项目或下一代版本。
2. DeepRare 作者、MAGIC-AI4Med、其所属机构或《Nature》对 OwlPath 进行了指导、合作、审核、背书或认证。
3. OwlPath 使用了 DeepRare 的代码、提示词、数据、模型、图像、Logo、运行轨迹或论文评测结果。
4. OwlPath 已复现 DeepRare 的论文数字、工具数量、生产微服务、本地大模型部署或临床表现。
5. 因为引用了 DeepRare 公开仓库，OwlPath 就已获得临床有效性、安全性、法规批准或医疗器械资格。
6. 开发模式的 `model_score` 是概率，或 Top-5 可替代微生物学检测、医生判断或治疗决策。
7. CI、容器、多 Provider 真实回归、多中心验证或临床发布门槛“已通过”，除非对应工件、日志和版本化报告已经在当前提交上完成验收。

## 7. 发布前核对清单

- [ ] `git grep -i deeprare` 只命中本文档、第三方声明、README 关系说明或必要的学术引用。
- [ ] 仓库中不存在 DeepRare 图片、Logo、GIF、数据、模型或代码片段。
- [ ] 所有演示病例都是纯虚构或明确去标识化的可发布材料。
- [ ] README 与演示材料没有任何合作、背书、复现或临床可用性的误导性表述。
- [ ] CI/容器/测试等宣称都有当前 commit 上的可复核工件支持。
