#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

source "$PROJECT_DIR/scripts/check-node.sh"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 Python 3。请先安装 Python 3.10 或更高版本。" >&2
  exit 1
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "当前 Python 为 $PYTHON_VERSION；OwlPath 需要 Python 3.10 或更高版本。" >&2
  exit 1
}

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "正在创建本地 Python 环境……"
  python3 -m venv "$VENV_DIR"
fi

VENV_PYTHON_VERSION="$("$VENV_DIR/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
"$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "现有 .venv 使用 Python $VENV_PYTHON_VERSION，无法安装当前安全依赖。" >&2
  echo "请先移动或删除项目根目录的 .venv，再使用 Python 3.10 或更高版本重新运行 make setup。" >&2
  exit 1
}

echo "正在安装后端依赖……"
RUNTIME_REQUIREMENTS="$PROJECT_DIR/backend/requirements.lock"
if [ ! -f "$RUNTIME_REQUIREMENTS" ]; then
  RUNTIME_REQUIREMENTS="$PROJECT_DIR/backend/requirements.txt"
fi
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -q --upgrade "pip==26.2.1"
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -q -r "$RUNTIME_REQUIREMENTS"
if [ -f "$PROJECT_DIR/backend/requirements-dev.txt" ]; then
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -q -r "$PROJECT_DIR/backend/requirements-dev.txt"
fi

echo "正在安装网页依赖……"
if [ -f "$PROJECT_DIR/frontend/package-lock.json" ]; then
  npm --prefix "$PROJECT_DIR/frontend" ci --no-audit --no-fund
else
  npm --prefix "$PROJECT_DIR/frontend" install --no-audit --no-fund
fi

echo "OwlPath 运行环境已准备完成。"
