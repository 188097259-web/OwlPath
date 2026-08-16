# OwlPath 研究、架构与治理资料包

> 文档状态：`owlpath.result.v3` + `owlpath.execution-graph.v4` 开发优先研究原型  
> 更新日期：2026-08-16  
> 使用边界：竞赛、软件开发和方法学研究，不是医疗器械

## 当前默认模式

普通网页当前只展示**开发推演模式**：粘贴纯虚构或已去标识化病例全文，5 位核心会诊专家固定参加，路由器再从 20 位动态专科专家中按病例证据最多召集 6 位，随后完成证据去重、分层外部检索、病原体总诊、确定性合同验证、独立审稿和最多一次修订。完成结果必须返回 5 个具体病原体，并以中英文展示；所有分数均为未校准模型分数。

开发模式把适用范围、时间不确定、OOD、校准器缺失和病例矛盾记录为观察告警，不因此弃答或只报病原大类。只有真实技术故障才使运行失败。这是一项为了先跑通和观察系统而采用的工程策略，**不是未来临床发布标准**。

严格临床分支仍保留在后端与回归测试中，包括当前决策时点、医生复核、外发同意、适用范围、安全裁决、粒度降级和弃答。普通前端暂时隐藏该入口。以下部分早期文档主要描述严格分支或未来验证路线，阅读时必须区分“当前开发默认”与“未来临床规则”。

> 开发输入的完整原文会发送给用户选择的模型 Provider。不得输入姓名、身份证号、电话、地址、病案号等直接身份信息；竞赛演示优先使用纯虚构文本。

## 文档导航

| 文件 | 当前用途 |
|---|---|
| [01_intended_use.md](01_intended_use.md) | 严格临床分支和未来研究范围契约；不作为开发模式阻断器 |
| [02_pathogen_label_adjudication.md](02_pathogen_label_adjudication.md) | 离线评价时区分致病、定植、污染和偶然检出 |
| [03_decision_time_event_ledger.md](03_decision_time_event_ledger.md) | 严格分支的当前时点与事件可见性方法；开发 v3 仍冻结输入快照 |
| [04_output_contract.md](04_output_contract.md) | 当前 `owlpath.result.v3` 开发输出及严格临床旧合同的边界 |
| [05_threat_model_privacy.md](05_threat_model_privacy.md) | 提示注入、API Key、外部数据传输、跨病例污染等风险 |
| [06_validation_release_gates.md](06_validation_release_gates.md) | 从开发原型走向回顾性、外部和前瞻性验证的门槛 |
| [07_competition_demo_script.md](07_competition_demo_script.md) | 按当前真实 UI 演示多专科 Agent、Top-5 与运行轨迹 |
| [08_agent_architecture.md](08_agent_architecture.md) | 当前 v4 执行图、三平面目标架构和节点轨迹契约 |
| [09_synthetic_regression_release_audit.md](09_synthetic_regression_release_audit.md) | 历史 v2 的 16 例纯虚构真实 Provider 回归记录；本仓库不包含原运行工件，不能独立复核其结果数字 |
| [10_clinician_interface_language_guide.md](10_clinician_interface_language_guide.md) | 临床主语言、工程细节第二层、状态翻译和页面阅读顺序 |
| [12_elite_clinical_specialist_prompt_library.md](12_elite_clinical_specialist_prompt_library.md) | 5 位核心专家与 20 位动态专家的中英文角色合同及运行时提示词核对 |
| [OwlPath_顶尖临床专科专家提示词手册_中英文_v2.0.docx](OwlPath_顶尖临床专科专家提示词手册_中英文_v2.0.docx) | 经过元数据清理和逐页渲染检查的双语 Word 阅读版 |
| [13_deeprare_inspiration_and_differences.md](13_deeprare_inspiration_and_differences.md) | DeepRare 公开仓库参考边界、独立开发声明与工程差异 |
| [14_reproducibility.md](14_reproducibility.md) | 代码、外部模型和医学结果三种不同层次的可复现边界 |

## 机器可读契约

| 路径 | 用途 |
|---|---|
| `config/agent_architecture.v1.json` | 当前实现架构、目标架构、成熟度和连线 |
| `config/clinical_terms.zh-en.v1.json` | 本地中英语名称与病原体术语缓存 |
| `config/intended_use.v1.json` | 严格临床分支的适用范围契约 |
| `config/model_output.schema.json` | 严格分支和历史示例的 v1 输出 Schema |
| `config/release_gates.v1.json` | 未来临床发布门槛摘要 |
| `config/local_priors.example.json` | 合成动态先验示例，不是当前 v3 在线模型 |
| `config/next_test_rules.example.json` | 历史严格分支的研究性下一检查规则示例 |

当前开发结果、Agent 请求和审稿合同由 `backend/app/models.py` 中的严格 Pydantic 模型定义；核心版本为：

- `owlpath.result.v3`
- `owlpath.execution-graph.v4`
- `owlpath.trace.v2`
- `owlpath.specialist.v2`
- `owlpath.critic.v1`

## 历史合成样例

`examples/cases/` 与 `examples/outputs/` 是早期严格临床五态合同的静态样例，用于验证时间闸门、Schema 和安全状态：

```bash
python3 examples/validate_examples.py
```

这些样例不是 v3 多专科 Agent 的实时结果，不会自动出现在网页中，也不能证明模型准确或完成临床校准。开发 v3 的行为应以后端测试和一次明确标注的真实 Provider 运行分别验证。

## 文档权威顺序

1. 当前开发执行节点和连线以 `config/agent_architecture.v1.json` 与后端冻结运行清单为准。
2. 当前开发结果格式以 `DevelopmentResultV3` 及其本地语义验证器为准。
3. 单次运行显示内容以 URL 中的 `run_id` 和后端持久化结果为准，不能由浏览器会话推断。
4. 严格临床范围与发布门槛以 `01_intended_use.md`、`config/intended_use.v1.json` 和 `06_validation_release_gates.md` 中更严格者为准。
5. 任何开发结果都不得被表述为确诊、临床概率或自动医嘱。
