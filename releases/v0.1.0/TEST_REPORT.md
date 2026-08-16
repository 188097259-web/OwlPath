# OwlPath v0.1.0 工程测试报告

> 状态：本地干净副本工程验证已完成；不是临床有效性、安全性或远程 CI 通过证明。

## 1. 报告信息

| 项目 | 记录 |
|---|---|
| 测试版本 | `v0.1.0` 公开比赛研究版 |
| 测试日期 | 2026-08-16 |
| 环境 | macOS arm64；最终临时副本复测使用 Python 3.12.13、Pydantic 2.13.4、Node.js 22.23.2、npm 11.12.1；另有 Python 3.10.21/3.11.11 锁定依赖回归记录；Docker CLI 不可用 |
| 文件身份 | `v0.1.0`；本报告及其余发布文件由同目录最终 `MANIFEST.sha256` 的逐文件哈希共同绑定，不在报告内嵌入会造成循环引用的 manifest 自身哈希 |
| 报告整理 | Codex 自动化协作工具，基于本地候选文件树和已知测试记录 |
| 独立技术复核 | 独立只读测试 Agent 复跑；不是人工临床、法务或渗透测试 |

## 2. 已知工程测试结果

候选源码被复制到不含 `.venv`、`node_modules`、`dist`、测试缓存和 TypeScript 构建缓存的临时目录；随后从零执行安装、测试、构建和静态审计。

| 检查项 | 仓库定义的复测入口 | 已知结果 | 本报告结论 |
|---|---|---|---|
| 干净副本安装 | 在临时目录安装锁定后端依赖并以 `npm ci` 安装前端 | 退出码 0；目标仓库未遗留 `.venv`、`node_modules` 或 `dist` | 通过 |
| 后端自动测试 | `make test-backend` | 240/240 通过 | 通过；存在 1 条未来迁移 `httpx2` 的非阻断弃用警告 |
| 前端自动测试 | `make test-frontend` | 41/41 通过 | 通过；包含执行图版本与轨迹版本分离映射回归 |
| 前端生产构建 | `make build` | Vite 成功处理 1581 个 modules | 通过 |
| 公开提示词与合同 | `verify_prompt_registry.py`、`export_public_schemas.py --check`、`verify_public_contracts.py` | 5 核心+20 动态角色一致；`result.v3` Schema 一致；`graph.v4`/`trace.v2`索引通过 | 通过；不公开供应商隐藏提示词或思维链 |
| 本地 HTTP 冒烟测试 | 端口 8765 的 `/api/health`、`/api/architecture`、`/` 和两项哈希静态资产 | 全部 HTTP 200；current 架构 46 节点/102 边，target 24 节点/29 边 | 通过；不代表全部交互或容器可用 |
| Python 锁文件审计 | `.venv/bin/python -m pip_audit -r backend/requirements.lock` | `No known vulnerabilities found` | 通过当前已知漏洞库检查 |
| Python 安装环境审计 | `.venv/bin/python -m pip_audit --local` | `No known vulnerabilities found` | 通过当前已知漏洞库检查 |
| 前端依赖漏洞检查 | `npm --prefix frontend audit --audit-level=low` | `found 0 vulnerabilities` | 通过当前已知漏洞库检查 |
| 发布前静态审计 | `./scripts/prepublish_audit.sh` | 仓库、样例、自测和 2 例公开夹具 dry-run 全部退出码 0 | 通过定义范围内的静态检查 |
| 本地 Git 归档复核 | `git fsck --full`、`git archive HEAD` 后重跑仓库审计和 `MANIFEST.sha256` | 单一根提交完整；归档 159 个文件；157 项 manifest 全部匹配 | 通过当前本地提交边界；未来提交需重跑 |
| 环境诊断 | `make doctor` | 在 Python 3.12/Node.js 22.23.2 环境四项通过；能正确拒绝系统 Python 3.9、Node 20 和缺失 Node | 通过 |

## 3. 明确未执行或未完成的测试

- 本机不可用 Docker，未进行 `Dockerfile` 或 `docker-compose.yml` 的构建、启动、健康检查和数据卷测试。
- 本次验证未调用任何真实模型 API；因此不能声称真实 Provider 兼容、稳定、可计费或端到端运行已通过。
- 未验证其他操作系统、浏览器、CPU 架构、并发压力、故障恢复、渗透测试或临床场景。

## 4. 数据与能力边界

- 公开 Agent 回归夹具为 2 例从零构造的纯合成场景，用于软件契约和流程检查，不是性能或临床验证。
- 50 例 Benchmark 是内部探索性汇总，不是临床验证，也不是本次工程测试的通过标准。

## 5. 结论

已知证据支持干净副本安装、后端 240/240、前端 41/41、Vite 1581 modules 构建、Python/NPM 已知漏洞检查以及本地 HTTP 冒烟通过。Docker、真实 Provider、多平台和临床验证仍未完成。本报告结论为：

`CLEAN_COPY_ENGINEERING_TESTS_PASSED_WITH_LIMITATIONS`
