# OwlPath 可复现性说明

> 适用版本：`v0.1.0`
> 目标：区分“代码可运行”、“外部模型可重放”和“医学结果可复现”三个不同层次。

## 1. 仓库内可复现的内容

不需要真实 Provider 或临床数据即可复现：

- 后端单元、合同和集成测试；
- 前端单元测试与生产构建；
- 两个从零构造的公开纯合成病例的离线合同检查；
- Provider 失败、Schema 失败、检索离线、审稿/修订和技术失败状态；
- 运行图、结果合同、Taxonomy 解析约束和公开轨迹隐私边界。

命令：

```bash
./scripts/setup.sh
make test
./scripts/prepublish_audit.sh
```

默认自动测试使用模拟 Provider 和模拟 HTTP 响应，不会产生真实模型费用。

## 2. 真实模型运行的可复现边界

同一输入、提示词和参数不能保证托管模型返回完全相同的候选，因为模型权重、服务端路由、采样、安全策略和外部检索结果可能变化。OwlPath 能冻结和记录的是：

- 输入快照和稳定 `source_fragment_id`；
- Agent 角色与提示词/合同版本；
- Provider 和模型标识、分配、请求序号、尝试次数和耗时；
- 检索工具、查询意图/哈希和来源元数据；
- 正规化结构化输出、确定性验证和完整性哈希。

因隐私和商业 Provider 条款，仓库不会发布 API Key、未过滤原始响应或隐藏思维链。

## 3. 真实 API 合成回归

运行器有显式费用闸：

```bash
python3 scripts/synthetic_regression.py \
  --fixture examples/public_synthetic_case_matrix.v1.json \
  --confirm-real-api
```

只有当操作者明确设置本地 Provider、理解费用和数据边界后才能执行。CI 不自动运行该命令。

## 4. 聚合基准的可复现边界

`benchmarks/aggregated_results.csv` 可复现图中的聚合比例，但无法在不公开逐例数据的情况下独立重算配对置信区间或仲裁正确性。因此必须同时阅读 `benchmarks/BENCHMARK_CARD.md`，不得将图表解释为普遍临床优越性。

## 5. 临床数据验证

MIMIC 和 DR.ECC 不随仓库分发。授权研究者未来复现验证时，应在各自获批的受保护环境中运行同一版本代码，并另行冻结：

- ICU 时间原点与 +24 小时决策截点；
- `event_time` 和 `available_time` 的防泄漏规则；
- 标本、培养、分子检测、定植/污染和多病原仲裁规则；
- 开发、时间外与地域外队列的患者级隔离；
- 模型、提示词、术语表、失败计入和统计方案。

可公开的是抽取/评价代码、版本化队列定义和披露审查后的聚合指标，而不是受限的病例级数据。

## 6. 发布工件

```bash
python3 scripts/repository_audit.py
python3 scripts/build_release_manifest.py
```

`MANIFEST.sha256` 证明哪些字节被纳入候选仓库，不证明医学结论正确。只有当所有审阅门禁完成、文件不再变更后，才应生成最终 manifest。
