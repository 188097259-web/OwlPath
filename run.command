#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
if ! "$PROJECT_DIR/scripts/service.sh" open; then
  printf '\nOwlPath 启动失败。请查看上方错误，按回车键关闭窗口。\n' >&2
  read -r
  exit 1
fi
