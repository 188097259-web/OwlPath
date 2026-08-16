# 纯合成验收样例

这里的病例与输出全部是人为编写的虚构数据，只用于验证研究系统的接口、时间闸门、五态决策和安全边界。它们不代表真实患者，也不能证明模型具有临床准确性。

`public_synthetic_case_matrix.v1.json` 是公开仓库默认的 Agent 回归夹具。其中两个场景由设计者从零构造，未引用 EHR、病例报告、数据库记录或任何真实个体。更大的内部压测矩阵不包含在公开仓库中。

这些 JSON 不会自动加载到网页，当前 UI 也没有离线回放功能。默认运行版没有临床验证校准器，因而不得发布物种级结论；这里的 `species_set` 仅用于覆盖未来 Schema 分支。

## 一键验收

在项目根目录运行：

```bash
python3 examples/validate_examples.py
python3 scripts/synthetic_regression.py --dry-run
```

第一条命令校验历史 Schema 样例；第二条命令只在本地解析公开纯合成夹具，不连接 API。真实 Provider 回归仍必须显式传入 `--confirm-real-api`，可能产生外部服务费用。

## 公开 Agent 回归夹具

| 病例 | 主要考点 |
|---|---|
| `PUBLIC-SYN-RESP-001` | 社区起病肺部感染、培养待回、具体 Top-5 |
| `PUBLIC-SYN-URINARY-002` | 梗阻性尿路感染、阴性暴露信息、具体 Top-5 |

脚本只依赖 Python 标准库，会检查：

- 所有 JSON 均可解析，病例与输出一一对应；
- 每个事件是否在当前决策时点 `t` 对医生可见，未来事件是否被排除；
- `infection_unlikely`、`species_set`、`category_only`、`more_information_needed`、`abstain` 五种主状态是否全部覆盖；
- 概率是否归一、Top-K 排名是否连续、证据引用是否可追溯；
- “下一项信息”是否来自专家白名单并明确不是医嘱；
- 输出是否符合 `config/model_output.schema.json`，以及是否包含禁止的诊疗字段；
- 动态本地先验是否为纯合成聚合数据，是否避免模型预测自我循环。

## 历史 Schema 样例覆盖

| 病例 | 主要考点 | 期望主状态 |
|---|---|---|
| `case_typical.json` | 用合成值覆盖未来物种预测集合 Schema；不是当前运行能力 | `species_set` |
| `case_category_only.json` | 证据只能支持病原大类，不能稳定到物种 | `category_only` |
| `case_conflicting_evidence.json` | 感染与肺水肿证据冲突，先补充最有价值信息 | `more_information_needed` |
| `case_immunosuppressed_ood.json` | 重度免疫抑制超出 v1 适用范围 | `abstain` |
| `case_noninfectious.json` | 非感染性解释更强，不继续强行排序病原体 | `infection_unlikely` |

每个输出中的概率只是为了测试数据结构而设置的数值，不是经过训练或验证的模型结果。

> 本研究系统尚未完成临床有效性验证，不是医疗器械，不可作为真实患者的独立诊断、检查医嘱或治疗依据。
