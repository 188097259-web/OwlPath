# 软件物料清单说明

本目录中的 SBOM 是有明确边界的依赖快照，不是候选仓库全部工具、全部平台和全部开发环境的完整物料清单，也不代表第三方许可证、出口管制、医学知识来源或云服务条款已经获得最终批准。

- `sbom-python.cdx.json`：由 `pip-audit 2.10.1` 根据 `backend/requirements.lock` 在 macOS arm64、Python 3.11.11 环境生成的**已激活后端运行时依赖** CycloneDX JSON（25 个组件）。它不包含 Python 3.10 才会激活的条件依赖，也不包含开发/文档工具。
- `sbom-frontend.cdx.json`：由 `npm 11.12.1` 根据 `frontend/package-lock.json` 生成的前端应用及开发依赖 CycloneDX JSON（122 个组件，包含锁文件中的跨平台可选包）。

生成命令：

```bash
.venv/bin/python -m pip_audit \
  -r backend/requirements.lock \
  --format cyclonedx-json \
  --output releases/v0.1.0/sbom-python.cdx.json

.venv/bin/python scripts/enrich_python_sbom_licenses.py --write

npm --prefix frontend sbom \
  --package-lock-only \
  --sbom-format cyclonedx \
  --sbom-type application \
  > releases/v0.1.0/sbom-frontend.cdx.json
```

Python 运行时许可证逐项证据见 [`backend/PYTHON_RUNTIME_LICENSES.md`](../../backend/PYTHON_RUNTIME_LICENSES.md)。许可证增强脚本只写入随安装包或指定 wheel 核验到的证据；遇到不明确关系时保留人工复核，不自行推断。

SBOM 是版本化盘点，不是漏洞为零或许可证兼容的证明；发布前仍需在目标 Python 3.10/3.11 和目标操作系统上分别生成或合并完整 SBOM，并对最终 commit 重新核对。
