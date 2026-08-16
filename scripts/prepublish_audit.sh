#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -n "${OWLPATH_PYTHON_BIN:-}" ]]; then
  OWLPATH_AUDIT_PYTHON="$OWLPATH_PYTHON_BIN"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  OWLPATH_AUDIT_PYTHON="$ROOT/.venv/bin/python"
else
  OWLPATH_AUDIT_PYTHON="python3"
fi

if ! "$OWLPATH_AUDIT_PYTHON" -c 'import pydantic' >/dev/null 2>&1; then
  echo "发布前检查需要项目 Python 依赖。请先运行 ./scripts/setup.sh，或通过 OWLPATH_PYTHON_BIN 指定已安装依赖的 Python。" >&2
  exit 2
fi

"$OWLPATH_AUDIT_PYTHON" scripts/repository_audit.py
"$OWLPATH_AUDIT_PYTHON" prompts/verify_prompt_registry.py
"$OWLPATH_AUDIT_PYTHON" schemas/export_public_schemas.py --check
"$OWLPATH_AUDIT_PYTHON" schemas/verify_public_contracts.py
"$OWLPATH_AUDIT_PYTHON" examples/validate_examples.py
"$OWLPATH_AUDIT_PYTHON" scripts/synthetic_regression.py --self-test
"$OWLPATH_AUDIT_PYTHON" scripts/synthetic_regression.py \
  --fixture examples/public_synthetic_case_matrix.v1.json \
  --dry-run

echo "PREPUBLISH_STATIC_AUDIT_OK"
