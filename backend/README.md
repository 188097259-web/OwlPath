# OwlPath backend

OwlPath 后端是一个 FastAPI + SQLite 的研究原型。当前普通网页使用**开发优先的 v3 多专科 Agent 流程**；严格临床流程仍保留在后端，但前端默认隐藏。

> 仅限纯虚构或已去标识化测试数据。系统未经过临床有效性验证，不是医疗器械，不得用于真实患者的独立诊断、检查或治疗决策。

## 推荐启动方式

从项目根目录运行：

```bash
cd OwlPath
./scripts/start.sh
```

它会构建前端，并在 `http://127.0.0.1:8000` 同时提供网页和 API。热更新开发模式使用：

```bash
./scripts/dev.sh
```

此时前端地址是 `http://127.0.0.1:5173`，后端 API 仍是 `http://127.0.0.1:8000`。

只启动后端：

```bash
cd OwlPath
./scripts/setup.sh
OWLPATH_DATA_DIR="$PWD/data" \
  ./.venv/bin/python -m uvicorn app.main:app \
  --app-dir backend --reload --host 127.0.0.1 --port 8000
```

API 文档位于 `http://127.0.0.1:8000/docs`。

## 开发 v3 编排

`POST /api/development/runs` 接收：

```json
{
  "text": "纯虚构或已去标识化病例全文",
  "provider_ids": ["prv_example"],
  "specialist_config_version": "owlpath.development-agents.v3"
}
```

Provider 必须已启用，并通过真实的纯合成连接测试。后端按权重冻结本次 Provider 清单，然后执行：

1. 保存不可变输入快照，并把原文编译为稳定 `source_fragment_id`；
2. 并行调用 5 位核心会诊专家，并由路由器从 20 位动态专科专家中按病例证据最多召集 6 位；
3. 证据委员会先对重复观察去重，检索规划器再从去标识化概念生成查询；文献层访问 Europe PMC/PubMed，公共卫生层访问 WHO DON 和版本化权威来源目录；
4. 由最高优先级 Provider 承担病原体总诊，生成具体 Top-5 草稿；
5. 本地确定性验证器检查数量、粒度、去重、排序、证据引用和 NCBI Taxonomy；
6. 独立审稿 Agent 检查病例事实是否遗漏和合同是否满足；
7. 必要时总诊最多修订一次；仍不合格时，只从已验证的专科候选池透明回退；
8. 生成 `owlpath.result.v3`、`owlpath.execution-graph.v4` 和 `owlpath.trace.v2`，计算完整性哈希并持久化。

无重试的正常路径有 7–13 次 LLM 调用；修订路径再增加 1 次。专科阶段 Provider 请求上限为 12，整次运行上限为 18，硬超时 420 秒。单 Agent 技术失败时会尝试本次清单中的下一个 Provider。任一检索源失败记录为 `retrieval_partial`，不会阻断开发结果。

## v3 完成合同

`completed` 或 `completed_with_warnings` 必须满足：

- `concrete_pathogens` 恰好 5 项，排名固定为 1–5；
- 每项是具体物种、物种复合群或明确病毒型别；
- 拉丁学名和 Taxonomy ID 唯一；
- 排名按非递增 `model_score` 排列；
- 每项至少引用一个病例来源片段，并记录提出它的专科 Agent；
- 病原大类、未知分数和共感染不占 Top-5；
- 核心摘要、病原体和下一项检查使用中英文结构。

开发模式不提供临床 `safety_action`，也不允许通过 `abstain=true`、空数组或“细菌/病毒”等类别词逃避合同。如果可用 Agent 候选不足 5 个，返回 `technical_failure`，而不是伪造病原体或写“转人工”。

## 主要 API

- `GET /api/health`
- `GET /api/architecture`
- `GET|POST|PATCH|DELETE /api/providers`
- `POST /api/providers/{provider_id}/test`
- `POST /api/development/runs`
- `POST /api/development-demo/runs`：兼容别名
- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/events`：SSE，支持事件 ID 续传
- `GET /api/runs/{run_id}/trace`
- `GET /api/runs/{run_id}/trace/nodes/{node_id}`
- `GET /api/runs/{run_id}/models`

原有 `/api/cases`、`POST /api/runs`、治理、评估和审计接口继续服务严格临床分支及只读兼容。开发新运行应调用专用 `/api/development/runs`，不能绕到严格病例接口。

## Provider 与真实外部调用

适配器支持 OpenAI Responses、Anthropic Messages、Gemini `generateContent`、OpenAI-compatible 和 Ollama。OpenAI-compatible 的 `options.response_format_mode` 支持：

- `json_object`：默认，随后由本地 Pydantic 和语义验证器严格检查；
- `json_schema`：服务端支持时可选择；
- `prompt_only`：仅靠提示约束，仍必须通过本地验证。

`json_object` 只说明返回值是 JSON，不保证它满足“具体病原体 Top-5”等医学语义，所以不能省略本地合同检查和审稿闭环。连接测试也只验证单次合成请求，不代表完整运行或医学质量已经通过。

## 隐私和轨迹

- 开发输入的完整原文会发送给所选模型 Provider，并保存在本地运行快照中；只能输入纯虚构或已去标识化文本。
- Europe PMC 和 PubMed 只接收由综合征、暴露和候选生成的泛化查询，不接收病例全文；对外轨迹只保存查询哈希和安全元数据。
- API Key 加密保存，不通过读取 API、SSE 或轨迹返回；未过滤的 Provider 原始响应和隐藏思维链也不会进入轨迹接口。
- 每个节点记录类型、角色、依赖、版本、状态、业务结果、时间、耗时、尝试次数、Provider、模型以及安全输入输出哈希。
- 纯虚构文本可以出现在 `demo_safe` artifact；严格临床 artifact 使用更窄的 `clinician_safe` 边界。
- SQLite 默认位于项目根目录 `data/owlpath.db`。可通过 `OWLPATH_DATA_DIR` 或 `OWLPATH_DB_PATH` 修改。未设置 `OWLPATH_MASTER_KEY` 时，后端会在数据库旁生成权限为 `0600` 的本机密钥文件。

启动脚本不会自动读取根目录 `.env`。需要环境变量时请先 `export`，或者由进程管理器注入。API Key 建议在网页中配置。

## 严格临床分支

严格分支继续执行当前决策时点、事件可见性、医生复核、外发同意、适用范围、校准/安全裁决和弃答逻辑。此次开发 v3 重构没有把这些规则删除，只是普通前端把严格入口隐藏。开发模式的观察告警不得被解释为严格临床模式的发布标准。

## 测试

```bash
cd OwlPath
make test
```

只运行后端：

```bash
cd OwlPath/backend
../.venv/bin/python -m pytest -q
```

自动化测试使用模拟 Provider 和模拟检索响应，不会产生真实模型费用。真实 Provider 的完整运行必须另行使用纯合成或已去标识化文本验证；不要把尚未执行的真实运行写成已成功。
