#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PORT="${OWLPATH_PORT:-8000}"
PERSISTENT_DATA_DIR="$HOME/Library/Application Support/OwlPath/data"
DEFAULT_DATA_DIR="$PROJECT_DIR/data"

if [ -f "$PERSISTENT_DATA_DIR/owlpath.db" ]; then
  DEFAULT_DATA_DIR="$PERSISTENT_DATA_DIR"
fi

# Do not start a second foreground server on top of the installed LaunchAgent.
if [ -f "$HOME/Library/LaunchAgents/com.owlpath.local.plist" ]; then
  echo "已检测到 OwlPath 常驻服务，正在确保它运行并打开网页。"
  exec "$PROJECT_DIR/scripts/service.sh" open
fi

source "$PROJECT_DIR/scripts/check-node.sh"

if [ ! -x "$VENV_DIR/bin/python" ] || [ ! -d "$PROJECT_DIR/frontend/node_modules" ]; then
  "$PROJECT_DIR/scripts/setup.sh"
fi

echo "正在构建本地网页……"
npm --prefix "$PROJECT_DIR/frontend" run build

open_browser() {
  local url="http://127.0.0.1:$PORT"
  local preview_url="$url/?ui=$(date +%s)"
  for _ in $(seq 1 60); do
    if curl -fsS "$url/api/health" >/dev/null 2>&1; then
      # A short cache-busting query ensures an existing browser tab cannot keep
      # showing an older SPA shell after a fresh frontend build.
      if command -v open >/dev/null 2>&1; then open "$preview_url"; fi
      return
    fi
    sleep 0.5
  done
}

open_browser &
echo "OwlPath 已启动：http://127.0.0.1:$PORT"
echo "按 Control-C 可安全停止。"

cd "$PROJECT_DIR"
OWLPATH_DATA_DIR="${OWLPATH_DATA_DIR:-$DEFAULT_DATA_DIR}" \
  "$VENV_DIR/bin/python" -m uvicorn app.main:app \
  --app-dir "$PROJECT_DIR/backend" --host 127.0.0.1 --port "$PORT"
