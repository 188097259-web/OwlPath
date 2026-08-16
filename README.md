# OwlPath｜鸮径

> **公开比赛研究版 `v0.1.0`：项目负责人已批准按本边界公开；GitHub 仓库地址为 [`188097259-web/OwlPath`](https://github.com/188097259-web/OwlPath)。**
> 本项目是病原体假设排序的研究原型，不是医疗器械，不得用于诊断、处方或替代临床判断。详见[医学用途与免责声明](MEDICAL_DISCLAIMER.md)。

公开仓库统一署名 **OwlPath Project Team**。项目创始人与临床负责人是 **DR.ECC 创始人**；其真实姓名和身份已向比赛组委会披露，公开仓库因个人隐私不展示姓名、机构或私人邮箱。

OwlPath 把“病原学结果尚未返回时，如何系统地缩小具体病原体范围”变成一条可运行、可追溯、可反驳的多 Agent 研究工作流。它不是“给出一个答案的医疗聊天机器人”：系统会保留原始证据片段，召集不同专业视角，检索外部线索，生成恰好 5 个具体病原体假设，再经过确定性合同校验和独立审稿。

[English README](README.en.md) · [架构文档](docs/08_agent_architecture.md) · [提示词手册](docs/12_elite_clinical_specialist_prompt_library.md) · [双语 Word 手册](docs/OwlPath_顶尖临床专科专家提示词手册_中英文_v2.0.docx) · [基准说明](benchmarks/BENCHMARK_CARD.md) · [安全政策](SECURITY.md)

![OwlPath 专家会诊工作流](assets/screenshots/workflow-expert-registry.png)

## 它解决什么

在 ICU 、急诊或早期住院阶段，治疗往往必须在培养、分子检测或药敏结果返回前启动。OwlPath 尝试在一个冻结决策时点上，完成三件事：

1. 把病例中的时间、宿主、部位、暴露、实验室、影像、微生物和治疗信息分工审阅；
2. 给出可验证的**具体病原体假设 Top-5**，而不是“细菌/病毒”这类空泛大类；
3. 说清每个假设来自哪段病例、哪个专家 Agent 和哪个术语记录；如检索命中则关联外部文献线索，无命中时明确显示覆盖不足告警。

完成运行中的分数均为**未校准模型分数**，不是临床概率；固定 Top-5 也不等于确诊 5 种感染。

## 当前真实执行链

```text
原始病例全文
  ↓
稳定来源片段 + 冻结输入快照
  ↓
复杂度/专病路由
  ↓
5 位固定核心专家 + 从 20 位动态专家中最多选 6 位
  ↓
证据委员会去重 + 检索规划
  ↓
文献/公共卫生线索检索 + 证据核验
  ↓
病原体总诊 Agent
  ↓
NCBI Taxonomy 解析 + 确定性 Top-5 合同
  ↓
独立审稿 Agent
  ↓
必要时最多修订一次
  ↓
双语结果 + 执行图 + 哈希完整性记录
```

开发路径使用 `owlpath.result.v3`、`owlpath.execution-graph.v4` 和 `owlpath.trace.v2`。一次运行选择 5–11 个临床专家角色；专科阶段最多 12 次 Provider 请求，全程真实网络请求上限 18 次，开发运行硬截止 420 秒。

### 版本为什么不只有一个数字

这些版本描述不同对象，不能互相替代：

| 版本轴 | 当前值 | 含义 |
|---|---|---|
| 公开比赛研究版 | `v0.1.0` | 首个获批公开的研究软件版本；不是临床发布 |
| 前后端软件包 | `0.1.0` | Python/Node 包版本 |
| HTTP 服务 | `0.1.0-research` | `/api/health` 报告的研究服务版本 |
| 开发 Agent 引擎 | `0.2.0-development-agents` | v3 开发运行的编排实现版本 |
| 治理声明 | `0.2.0-research` | 随运行冻结的研究治理规则版本 |
| 架构目录 | `3.0.0-elite-specialist-team` | 5 位核心、20 位动态专家的架构配置版本 |
| 结果 / 执行图 / 轨迹合同 | `result.v3` / `execution-graph.v4` / `trace.v2` | 三类不同 API 数据结构版本 |

未来版本可以同步升级这些值，但不应为追求数字一致而混淆各自语义。

## 项目完整性

这份公开比赛研究仓库包含：

- FastAPI + SQLite 后端，React + TypeScript 前端；
- OpenAI Responses、Anthropic、Gemini、OpenAI-compatible 和 Ollama Provider 适配；
- 5 位核心专家、20 位动态专家注册表与双语提示词手册；
- 术语解析、证据板、联邦检索、合同验证、审稿/修订与运行轨迹；
- 后端、前端、合同、失败隔离、网络安全和隐私回归测试；
- 纯合成公开样例、聚合基准说明、Docker 入口、CI 和发布前审计脚本；
- 威胁模型、医疗声明、贡献规则、安全报告方式与发布审阅清单。

## 五分钟本地运行

环境：Python 3.10+（CI 覆盖 3.10、3.11、3.12；本地参考环境使用 3.11）、Node.js 22.18+。前端测试直接校验 TypeScript 源文件，因此依赖 Node 22.18 起默认启用的类型剥离能力；Python 3.9 无法安装当前已修复安全告警的依赖组合。

```bash
git clone https://github.com/188097259-web/OwlPath.git OwlPath
cd OwlPath
./scripts/setup.sh
./scripts/start.sh
```

打开 `http://127.0.0.1:8000`。开发热更新使用 `./scripts/dev.sh`，环境检查使用 `./scripts/doctor.sh`。macOS 用户还可使用 `./scripts/service.sh install` 安装本机 LaunchAgent；详情见 [后端与运行说明](backend/README.md)。

也可以用 Docker：

```bash
docker compose up --build
```

Compose 默认只绑定 `127.0.0.1:8000`，不把服务暴露到局域网。

本次验证环境没有安装 Docker CLI，因此 Dockerfile 与 Compose 已做静态检查，但镜像冷启动尚未实测。这是已公开的工程限制，不得改写为“Docker 已验证”。

## 模型配置、隐私与费用

启动后在网页“模型与 API”页面添加 Provider、模型 ID 和 API Key。API Key 仅存入本地加密密钥库，不会回显。真实外部 Provider 可能产生费用；自动测试使用模拟 Provider，不发起收费请求。

如果输入不是纯合成文本，必须先确认数据授权、去标识化级别和 Provider 数据政策。本开源版不包含 MIMIC、DR.ECC 或任何患者级数据；也不包含 API Key、本地数据库、未过滤模型原始响应或隐藏思维链。

## 运行与追溯

每次运行 ID 写入 URL，是当前页面的唯一权威来源：

```text
#/runs/{run_id}/progress
#/runs/{run_id}/result
#/runs/{run_id}/result?tab=trace
#/runs/{run_id}/compare
```

主要 API：`POST /api/development/runs`、`GET /api/runs/{run_id}`、`GET /api/runs/{run_id}/events`、`GET /api/runs/{run_id}/trace`、`GET /api/runs/{run_id}/trace/nodes/{node_id}` 和 `GET /api/architecture`。轨迹只保存结构化专业意见、候选、证据、结果和安全错误码，不展示模型隐藏思维链。

## 测试与发布前审计

```bash
make test
python3 scripts/synthetic_regression.py --self-test
python3 scripts/synthetic_regression.py --fixture examples/public_synthetic_case_matrix.v1.json --dry-run
python3 scripts/repository_audit.py
```

`make test` 运行后端全量测试、前端测试和生产构建。真实 API 合成回归需要显式费用确认闸，不会在 CI 中自动执行。

## 目录

```text
backend/      FastAPI、Provider 适配、编排、检索、术语和持久化
frontend/     React + TypeScript 研究控制台
config/       Agent 注册、术语表、用途边界和发布门
prompts/      提示词源文件索引
schemas/      v3 结果/执行合同索引
docs/         架构、隐私、评价、演示与提示词手册
examples/     明确纯合成、可公开的样例
benchmarks/   仅聚合、非患者级的内部探索性比较
scripts/      安装、运行、回归和仓库审计
releases/     版本化发布记录、SBOM 与 SHA-256 manifest
data/         运行时数据；除 README 外不进入 Git
```

## 与 DeepRare 的关系

本仓库参考了 DeepRare 公开论文与固定仓库提交中的研究软件组织方式，以及“候选生成后再做候选级证据核对与有限反思”的高层研究思想；OwlPath 的感染病原体架构与代码均为独立设计，**没有包含或复制 DeepRare 的代码、提示词、图像、Logo、数据或论文结果数字**。详见 [借鉴与差异](docs/13_deeprare_inspiration_and_differences.md) 和 [第三方说明](THIRD_PARTY_NOTICES.md)。

## 当前证据边界

- 多 Agent 执行、合同、失败隔离和可追溯性已有工程测试覆盖；
- `benchmarks/` 只包含 50 例内部初步比较的聚合数，不包含病例级记录，不构成临床有效性验证；
- MIMIC 和 DR.ECC 验证尚未作为本仓库发布结果；
- 外部文献当前主要是题名/元数据级线索，不等于全文证据蕴含验证；
- 使用 **OwlPath Project Team** 作为公开署名；真实姓名、机构与私人联系方式不在仓库中披露；
- 合成样例的持续人工复核、基准方法补充、Docker 冷启动和真实 Provider 验证仍是后续工作。

一般问题请使用 [GitHub Issues](https://github.com/188097259-web/OwlPath/issues)；敏感安全问题请使用 [GitHub Private Vulnerability Reporting](https://github.com/188097259-web/OwlPath/security/advisories/new)，不要在公开 Issue 中粘贴密钥、漏洞细节或临床文本。

## 许可

OwlPath 自有代码、文档和本仓库中标明为项目自有的视觉资产，已由项目负责人授权按 [MIT License](LICENSE) 公开。医学知识、指南、数据库、第三方 API、模型及软件依赖仍受各自的许可、隐私政策和使用条款约束。
