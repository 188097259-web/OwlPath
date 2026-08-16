# OwlPath 运行时提示词索引

> 版本：`owlpath.prompt-registry.v1`
>
> 对应开发 Agent 配置：`owlpath.development-agents.v3`
>
> 用途：比赛公开版的审计、复现和代码导航

## 先说清楚：真正运行的提示词在哪里

OwlPath 没有为公开展示再造一套与程序脱节的“演示提示词”。真正会被运行时组合并发送给用户所选模型的任务指令，以下列代码为唯一权威来源：

| 功能 | 运行时权威来源 | 主要符号 |
|---|---|---|
| 专科、总诊、审稿指令 | `backend/app/providers.py` | `DEVELOPMENT_SPECIALIST_INSTRUCTION`、`DEVELOPMENT_SYNTHESIS_INSTRUCTION`、`DEVELOPMENT_CRITIC_INSTRUCTION` |
| 25 个专科角色的具体职责 | `backend/app/providers.py` | `_SPECIALIST_ROLE_FOCUS` |
| 原始病例与结构化附件的组装 | `backend/app/providers.py` | `_development_primary_prompt` |
| JSON 对象紧凑合同 | `backend/app/providers.py` | `_development_specialist_contract`、`_development_synthesis_contract`、`_development_critic_contract` |
| Provider 适配和最终消息组装 | `backend/app/providers.py` | `ProviderClient.invoke_development_*`、`ProviderClient._invoke_structured` |
| 5 个核心专家、20 个动态专家及路由规则 | `backend/app/engine.py` | `DEVELOPMENT_CORE_SPECIALIST_ROLES`、`DEVELOPMENT_DYNAMIC_SPECIALIST_ROLES`、`select_dynamic_development_roles` |
| 执行图中的角色、版本与 Provider 冻结 | `backend/app/engine.py` | `build_development_execution_manifest` |
| 提示词输入/输出类型和本地语义验证 | `backend/app/models.py` | `DevelopmentSpecialist*`、`DevelopmentSynthesis*`、`DevelopmentCritic*`、`validate_development_top5` |

`docs/12_elite_clinical_specialist_prompt_library.md` 和中英文 Word 手册是给临床与评审人阅读的解释材料，不是另一份运行时模板。

## 一次调用如何组装

1. 专科 Agent：通用专科指令＋当前角色编号＋该角色职责＋病例全文＋补充结构＋`owlpath.specialist.v2`输出合同。
2. 总诊 Agent：总诊指令＋病例全文＋专家意见＋证据板＋检索来源＋`owlpath.synthesis-draft.v1`输出合同。
3. 审稿 Agent：独立审稿指令＋原始证据＋草稿＋确定性验证问题＋`owlpath.critic.v1`输出合同。
4. 修订 Agent：复用总诊指令和全新输入上下文，额外带入首轮草稿、确定性问题与审稿意见；最多修订一次。

模型返回后还必须经过 Pydantic 结构验证、具体 Top-5 语义验证、NCBI Taxonomy 解析和证据引用核对。提示词本身不能替代这些确定性检查。

## 角色与版本

- 核心专家：5 个，每次新运行都参加。
- 动态专家：20 个，按原始文本线索最多选择 6 个。
- 角色配置：`owlpath.development-agents.v3`。
- 专科节点：`owlpath.development-specialist.v3`。
- 总诊/单次修订节点：`owlpath.development-synthesis.v2`。
- 独立审稿节点：`owlpath.development-critic.v2`。
- 结果合同：`owlpath.result.v3`。
- 执行图：`owlpath.execution-graph.v4`。
- 公开轨迹：`owlpath.trace.v2`。

完整的角色编号、中英文名称、运行时版本、权威来源符号和源文件 SHA-256 位于 [runtime_prompt_registry.v1.json](runtime_prompt_registry.v1.json)。这个 JSON 是公开审计索引，程序运行时仍以上述代码符号为权威来源。

## 验证哈希和角色同步

在仓库根目录执行：

```bash
python3 prompts/verify_prompt_registry.py
```

该脚本只使用 Python 标准库，会：

- 重算每个权威源文件的 SHA-256；
- 确认公开索引中的 5＋20 角色与运行时常量完全一致；
- 确认关键提示词、合同和调用函数仍存在。

也可手工核对单个文件：

```bash
shasum -a 256 backend/app/providers.py
```

只要任一权威源文件发生实质修改，就必须重新生成或更新索引哈希，同时评估是否需要提升 Agent 版本并运行回归测试。

## 公开边界

本目录不会也不应包含：

- API Key、私有端点、机构内部指令或患者信息；
- 任何 Provider 的私有系统指令、隐藏提示词或平台内部实现；
- 模型隐藏思维链、`reasoning_content` 或未过滤的原始响应；
- 任何真实 MIMIC、DR.ECC 或本地患者数据。

可公开的可追溯信息是：结构化专业观察、病原候选、病例片段 ID、联网证据引用、确定性验证问题、审稿结论与完整性哈希。
