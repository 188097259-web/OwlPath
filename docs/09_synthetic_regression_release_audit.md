# OwlPath 纯虚构病例回归与开发版验收

> 历史记录日期：2026-08-11
> 文档身份：**历史 v2 开发环境记录；不是 `v0.1.0` 发布证据**
> 当时验收结论：**开发环境工程验收通过**
> 非结论：本轮不能证明临床准确性、概率校准或医疗器械可用性。

## 1. 测试范围

回归矩阵包含 16 个显式标记为纯虚构的病例，覆盖呼吸、中枢神经、皮软组织、腹腔、泌尿、导管相关、免疫抑制/真菌、水体/鱼类、动物分娩物、热带旅行、病毒-细菌共感染、非感染模拟、抗菌药相关结肠炎、心内膜炎、误吸/化学性肺炎和人工关节感染。

本节来自 2026-08-11 的本地历史验收记录。历史 Provider 运行工件可能包含不适合公开的运行材料，已按发布边界从本次公开仓库排除；因此下述数字**不能仅凭本仓库独立复核，也不属于本次发布证据**。本仓库只提供从零构造的公开合成样例与离线验收器。

## 2. 当前验收结果

> 历史快照：本节记录 2026-08-11 时的 v2/5-专科执行图，仅供追溯；当前新运行已迁移到 `owlpath.execution-graph.v4`，固定 5 位核心专家，并从 20 位动态专家中最多选择 6 位。下列数字不能当作当前 v4 执行图的实测结果。

- 16/16 个病例均至少有一次已持久化运行通过当前版验收器。
- 每次通过运行均包含恰好 5 个具体、唯一的命名病原体，大类和未知不占 Top-5。
- 所有候选均通过确定性 Taxonomy 解析，并保留病例来源片段、真实提出该候选的专科 Agent 和文献来源关联。
- 当时运行图为 `owlpath.execution-graph.v2`，轨迹为 `owlpath.trace.v2`；当时最新两次运行均为 18 节点、29 条边，manifest 完整性校验通过。
- 历史完整复核表未随本次公开仓库分发；不得把这组历史汇总写成当前 v4 的可复现性能结果。

## 3. 本轮通过实际运行发现并修复的缺陷

- Provider 输出截断、Schema 别名、客观审稿意见误判和无效修订覆盖有效初稿。
- Taxonomy 精确名称/层级核验、网络瞬时失败、属级或混合群假冒物种级，以及候选池回退时的重复、伪来源和分数顺序问题。
- 单 Provider 网络抖动时当时的五个专科 Agent 同时失败：v2 当时加入并发上限、DNS single-flight、有界重试、8 次真实请求硬上限和 300 秒总截止；这些数字已被当前 v4 开发执行图的 12 次专科阶段上限、18 次整体上限和 420 秒截止取代。
- 硬超时取消后模型输出残留 `running`，以及跨 event-loop 的并发槽内存残留。
- 结果链接刷新后丢失 `?tab=agents/evidence/trace`，现已修复并加入路由回归测试。
- 回归运行器中字面匹配造成的假失败：现使用不会外发的语义行为 oracle，并保留历史原工件。

## 4. 网页与服务验收

- 结果页直接链接、刷新、复制和两标签页并行打开时，URL 中的 `run_id` 均是唯一权威来源，没有串病例。
- 医生视图、Agent 协作、文献与诊断、真实运行轨迹和完整目标/当前架构页均可访问。
- 390 px 宽度下结果页和架构页无水平溢出；实测浏览器控制台无错误。
- macOS LaunchAgent 常驻服务从 `Application Support` 运行，数据和主密钥文件权限为 `0600`，数据目录为 `0700`，SQLite integrity/quick/foreign-key 检查通过。

## 5. 安全与隐私检查

最新两次真实 Provider 运行的 run、models、trace 及全部 18 个节点详情接口已做递归扫描，未出现 API Key、Authorization/Bearer、未过滤 Provider 原始响应、加密密钥字段或取消异常原文。

文献检索查询只由去标识化的综合征/暴露/规范拉丁名构建，回归运行器也检查了隐藏预期不会进入 Provider 请求或对外工件。

## 6. 已知但不影响当前本地开发验收的局限

1. 分数仍是未校准模型分数；不能称为临床概率。
2. 本轮只验收了工程合同和可追溯性；真实准确性需要有病原学真值的多中心回顾性和前瞻性验证。
3. 当前真实回归使用一个 DeepSeek Provider；多 Provider 轮转和 failover 已由无网络单元/集成测试验证，尚未做多个付费 Provider 的真实并行回归。
4. 当前仅绑定 `127.0.0.1`。若进入云/VPC 或允许不可信自定义 Provider，仍需通过 Provider 域名白名单、出口防火墙/代理或安全 IP pinning 缩小 DNS 验证与真实连接之间的 rebinding 窗口。
5. 系统 DNS resolver 线程在超时后不能被 Python 强制终止；Provider 响应大小目前是完整读取后校验。后续应加入隔离 DNS executor/全局限流和流式响应上限。
6. 部分候选在文献检索短暂不可用时仅有部分文献覆盖；系统会显式告警，不会伪造引用。

## 7. 可复现命令

```bash
make test
python3 scripts/synthetic_regression.py --self-test
python3 scripts/synthetic_regression.py --fixture examples/public_synthetic_case_matrix.v1.json --dry-run
./scripts/service.sh status
```

真实 Provider 回归会产生费用，必须显式确认：

```bash
make synthetic-regression CONFIRM_REAL_API=1 ARGS='--fixture examples/public_synthetic_case_matrix.v1.json --case-id PUBLIC-SYN-RESP-001'
```
