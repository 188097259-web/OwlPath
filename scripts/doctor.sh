#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FAILED=0

check() {
  if "$@" >/dev/null 2>&1; then
    printf '✓ %s\n' "$1"
  else
    printf '✗ %s\n' "$1"
    FAILED=1
  fi
}

echo "OwlPath 环境检查"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "✓ Python 3.10+"
else
  echo "✗ Python 3.10+（当前安全依赖不支持 Python 3.9）"
  FAILED=1
fi
if bash "$PROJECT_DIR/scripts/check-node.sh" >/dev/null 2>&1; then
  echo "✓ Node.js 22.18+ / npm"
else
  echo "✗ Node.js 22.18+ / npm"
  FAILED=1
fi

if [ -x "$PROJECT_DIR/.venv/bin/python" ] && "$PROJECT_DIR/.venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "✓ Python 3.10+ 本地环境"
else
  echo "✗ Python 3.10+ 本地环境（请运行 ./scripts/setup.sh）"
  FAILED=1
fi

if [ -d "$PROJECT_DIR/frontend/node_modules" ]; then
  echo "✓ 网页依赖"
else
  echo "✗ 网页依赖（请运行 ./scripts/setup.sh）"
  FAILED=1
fi

exit "$FAILED"
