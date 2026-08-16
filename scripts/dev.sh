#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
BACKEND_PORT="${OWLPATH_PORT:-8000}"
FRONTEND_PORT="${OWLPATH_FRONTEND_PORT:-5173}"

source "$PROJECT_DIR/scripts/check-node.sh"

if [ ! -x "$VENV_DIR/bin/python" ] || [ ! -d "$PROJECT_DIR/frontend/node_modules" ]; then
  "$PROJECT_DIR/scripts/setup.sh"
fi

cleanup() {
  if [ -n "${BACKEND_PID:-}" ]; then kill "$BACKEND_PID" 2>/dev/null || true; fi
  if [ -n "${FRONTEND_PID:-}" ]; then kill "$FRONTEND_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

cd "$PROJECT_DIR"
OWLPATH_DATA_DIR="${OWLPATH_DATA_DIR:-$PROJECT_DIR/data}" \
  "$VENV_DIR/bin/python" -m uvicorn app.main:app \
  --app-dir "$PROJECT_DIR/backend" --host 127.0.0.1 --port "$BACKEND_PORT" --reload &
BACKEND_PID=$!

VITE_API_PROXY_TARGET="http://127.0.0.1:$BACKEND_PORT" \
  npm --prefix "$PROJECT_DIR/frontend" run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

echo "OwlPath 开发模式已启动：http://127.0.0.1:$FRONTEND_PORT"
wait "$BACKEND_PID" "$FRONTEND_PID"
