# OwlPath 公开合同与 Schema 入口

> 当前开发结果：`owlpath.result.v3`
>
> 当前开发执行图：`owlpath.execution-graph.v4`
>
> 当前公开轨迹：`owlpath.trace.v2`

这个目录现在提供可机器读取、可核对、不与运行时源代码争抢“权威来源”的公开入口：

| 文件 | 作用 |
|---|---|
| [contracts.index.v1.json](contracts.index.v1.json) | 三个版本合同的机器可读索引，包含权威代码符号、API 入口、哈希与验证方式 |
| [owlpath.result.v3.schema.json](owlpath.result.v3.schema.json) | 从 `DevelopmentResultV3` Pydantic 模型机械生成的 JSON Schema |
| [export_public_schemas.py](export_public_schemas.py) | 从当前运行时模型重新生成结果 Schema |
| [verify_public_contracts.py](verify_public_contracts.py) | 核对版本常量、源文件哈希、关键符号和已生成 Schema |

## 为什么只对 result.v3 发布独立 JSON Schema

`owlpath.result.v3` 已有明确的 Pydantic 模型 `DevelopmentResultV3`，所以可以从真实运行时源代码直接生成 Schema，不需要手工复制一套容易漂移的定义。

`owlpath.execution-graph.v4` 由 `build_development_execution_manifest()` 根据本次病例、动态专家选择和冻结 Provider 实时构建。`owlpath.trace.v2` 由公开 API 对数据库节点和可公开 artifact 做脱敏投影。当前后端并没有为它们定义独立 Pydantic 输出模型。

因此，本公开版不会手写两份看似正式、实际可能与 API 漂移的独立 Schema。它们的可验证入口已在 `contracts.index.v1.json` 中精确指向：

- 执行图的构建函数、版本常量、持久化哈希与预运行完整性比对；
- 轨迹 API、节点投影、artifact 投影、脱敏函数和 SHA-256 完整性字段。

这是“准确索引”，不是虚构一个尚未存在的运行时模型。

## 生成与验证

先安装后端依赖，然后在仓库根目录运行：

```bash
python3 schemas/export_public_schemas.py --check
python3 schemas/verify_public_contracts.py
```

如果 `DevelopmentResultV3` 发生了经批准的修改，重新生成：

```bash
python3 schemas/export_public_schemas.py
```

`--check` 不会修改文件，只检查已提交 Schema 是否与当前 Pydantic 模型逐字节一致。

## 验证能力边界

JSON Schema 可验证字段、类型、枚举、范围、长度和嵌套结构，但不等于完整的 OwlPath 语义验证。例如：

- 已完成运行必须恰好有 5 个具体、唯一病原体；
- 排名必须为 1–5，且`model_score`按排名非递增；
- 不能用“细菌”“病毒”“未知病原”占据 Top-5；
- 每个候选必须能回溯到真实 `source_fragment_id`、提出 Agent 和已解析 Taxonomy；
- 公开轨迹不得含 API Key、原始 Provider 响应或隐藏思维链。

这些还由 `DevelopmentResultV3` 的 Pydantic model validator、`validate_development_top5()`、运行引擎的完整性校验和 API 脱敏投影联合强制。通过 JSON Schema 只能说明“结构合法”，不能说明“医学正确”。
