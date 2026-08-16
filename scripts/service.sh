#!/usr/bin/env bash
set -euo pipefail

# Finder launches .command files with a minimal PATH.  Keep the common
# Homebrew locations explicit so first-time setup/build works when double-clicked.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

# macOS LaunchAgents cannot reliably read projects stored in Documents because
# they do not inherit Terminal's Files & Folders permission.  The source stays
# in the workspace, while launchd runs a deploy-only copy from Application
# Support.  Persistent data lives beside that runtime and is never overwritten
# by a code update.
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_PATH="$PROJECT_DIR/scripts/service.sh"
SOURCE_VENV="$PROJECT_DIR/.venv"
SOURCE_FRONTEND_INDEX="$PROJECT_DIR/frontend/dist/index.html"
SOURCE_DATA_DIR="$PROJECT_DIR/data"

APP_SUPPORT_ROOT="$HOME/Library/Application Support/OwlPath"
LOCK_FILE="$APP_SUPPORT_ROOT/service.lock"
RUNTIME_DIR="$APP_SUPPORT_ROOT/runtime"
PREVIOUS_RUNTIME_DIR="$APP_SUPPORT_ROOT/runtime.previous"
RUNTIME_VENV="$RUNTIME_DIR/.venv"
RUNTIME_PYTHON="$RUNTIME_VENV/bin/python"
RUNTIME_BACKEND="$RUNTIME_DIR/backend"
RUNTIME_FRONTEND_INDEX="$RUNTIME_DIR/frontend/dist/index.html"
DATA_DIR="$APP_SUPPORT_ROOT/data"

PORT="${OWLPATH_PORT:-8000}"
URL="http://127.0.0.1:$PORT"
LABEL="com.owlpath.local"
USER_ID="$(/usr/bin/id -u)"
DOMAIN="gui/$USER_ID"
SERVICE_TARGET="$DOMAIN/$LABEL"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/OwlPath"
STDOUT_LOG="$LOG_DIR/owlpath.stdout.log"
STDERR_LOG="$LOG_DIR/owlpath.stderr.log"
EXPECTED_SERVICE="OwlPath（鸮径）"
EXPECTED_VERSION="0.1.0-research"

say() { printf '%s\n' "$*"; }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }

require_macos() {
  [ "$(/usr/bin/uname -s)" = "Darwin" ] || die "LaunchAgent 常驻模式仅支持 macOS"
  [ -x /bin/launchctl ] || die "未找到 launchctl"
  [ -x /usr/bin/plutil ] || die "未找到 plutil"
  [ -x /usr/sbin/lsof ] || die "未找到 lsof"
  [ -x /usr/bin/python3 ] || die "未找到 macOS Python"
}

build_frontend() {
  source "$PROJECT_DIR/scripts/check-node.sh"
  if [ ! -d "$PROJECT_DIR/frontend/node_modules" ]; then
    "$PROJECT_DIR/scripts/setup.sh"
  fi
  say "正在构建 OwlPath 网页……"
  npm --prefix "$PROJECT_DIR/frontend" run build
}

ensure_source_runtime() {
  if [ ! -x "$SOURCE_VENV/bin/python" ]; then
    say "未找到 Python 本地环境，正在安装依赖……"
    "$PROJECT_DIR/scripts/setup.sh"
  fi
  if [ ! -f "$SOURCE_FRONTEND_INDEX" ]; then
    build_frontend
  else
    say "复用已构建的网页：$SOURCE_FRONTEND_INDEX"
  fi
}

is_loaded() {
  /bin/launchctl print "$SERVICE_TARGET" >/dev/null 2>&1
}

service_pid() {
  /bin/launchctl print "$SERVICE_TARGET" 2>/dev/null \
    | /usr/bin/awk '/^[[:space:]]*pid = / {print $3; exit}'
}

health_ok() {
  local response
  response="$(/usr/bin/curl -fsS --max-time 2 "$URL/api/health" 2>/dev/null)" || return 1
  printf '%s' "$response" | /usr/bin/python3 -c '
import json, sys
try:
    payload = json.load(sys.stdin)
except (TypeError, ValueError):
    raise SystemExit(1)
valid = (
    payload.get("status") == "ok"
    and payload.get("service") == "OwlPath（鸮径）"
    and payload.get("version") == "0.1.0-research"
)
raise SystemExit(0 if valid else 1)
' >/dev/null 2>&1
}

frontend_ok() {
  local response
  response="$(/usr/bin/curl -fsS --max-time 3 "$URL/" 2>/dev/null)" || return 1
  printf '%s' "$response" | /usr/bin/grep -F '<div id="root"></div>' >/dev/null 2>&1
}

listener_owned_by_service() {
  local pid listeners
  pid="$(service_pid)"
  [ -n "$pid" ] || return 1
  listeners="$(/usr/sbin/lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  printf '%s\n' "$listeners" | /usr/bin/grep -Fx "$pid" >/dev/null 2>&1
}

ready_ok() {
  health_ok && frontend_ok && listener_owned_by_service
}

port_is_in_use() {
  /usr/sbin/lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
}

wait_for_port_free() {
  local attempts="${1:-60}" attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if ! port_is_in_use; then return 0; fi
    /bin/sleep 0.25
  done
  return 1
}

assert_port_available() {
  if port_is_in_use; then
    say "端口 $PORT 已被其他进程占用："
    /usr/sbin/lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2 || true
    die "为避免误停其他程序，OwlPath 未继续启动"
  fi
}

wait_for_ready() {
  local attempts="${1:-80}" attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if ready_ok; then return 0; fi
    /bin/sleep 0.5
  done
  return 1
}

show_recent_logs() {
  if [ -f "$STDERR_LOG" ]; then
    say ""; say "最近错误日志：$STDERR_LOG"
    /usr/bin/tail -n 40 "$STDERR_LOG" || true
  fi
  if [ -f "$STDOUT_LOG" ]; then
    say ""; say "最近运行日志：$STDOUT_LOG"
    /usr/bin/tail -n 30 "$STDOUT_LOG" || true
  fi
}

stop_loaded_service() {
  if is_loaded; then
    /bin/launchctl bootout "$SERVICE_TARGET"
    wait_for_port_free 80 || die "旧 OwlPath 进程未在预期时间内退出"
  fi
}

finalize_runtime_deployment() {
  /bin/rm -rf -- "$PREVIOUS_RUNTIME_DIR"
}

rollback_runtime_deployment() {
  [ -d "$PREVIOUS_RUNTIME_DIR" ] || return 1
  /bin/rm -rf -- "$RUNTIME_DIR"
  /bin/mv "$PREVIOUS_RUNTIME_DIR" "$RUNTIME_DIR"
  return 0
}

activation_failed() {
  local message="$1" allow_rollback="${2:-no}"
  show_recent_logs
  if is_loaded; then /bin/launchctl bootout "$SERVICE_TARGET" 2>/dev/null || true; fi
  wait_for_port_free 80 || true

  if [ "$allow_rollback" = "yes" ] && rollback_runtime_deployment; then
    say "新版本启动失败，已回滚到上一个可运行副本。"
    if /bin/launchctl bootstrap "$DOMAIN" "$PLIST_PATH" 2>/dev/null && wait_for_ready 80; then
      die "$message；上一版本已自动恢复运行"
    fi
    if is_loaded; then /bin/launchctl bootout "$SERVICE_TARGET" 2>/dev/null || true; fi
    wait_for_port_free 80 || true
  fi
  die "$message；已自动停止服务，不会继续崩溃循环"
}

deploy_runtime() {
  ensure_source_runtime
  /bin/mkdir -p "$APP_SUPPORT_ROOT"

  local stage
  stage="$APP_SUPPORT_ROOT/.runtime-stage-$$"
  /bin/rm -rf -- "$stage" "$PREVIOUS_RUNTIME_DIR"
  /bin/mkdir -p "$stage/frontend"

  say "正在部署可后台访问的运行副本……"
  /usr/bin/ditto "$SOURCE_VENV" "$stage/.venv"
  /usr/bin/ditto "$PROJECT_DIR/backend" "$stage/backend"
  /usr/bin/ditto "$PROJECT_DIR/frontend/dist" "$stage/frontend/dist"
  /usr/bin/ditto "$PROJECT_DIR/config" "$stage/config"

  PYTHONPATH="$stage/backend" "$stage/.venv/bin/python" -c \
    'import fastapi, uvicorn, app.main' >/dev/null
  [ -f "$stage/frontend/dist/index.html" ] || die "部署副本缺少网页 index.html"

  if [ -d "$RUNTIME_DIR" ]; then /bin/mv "$RUNTIME_DIR" "$PREVIOUS_RUNTIME_DIR"; fi
  if ! /bin/mv "$stage" "$RUNTIME_DIR"; then
    if [ -d "$PREVIOUS_RUNTIME_DIR" ]; then /bin/mv "$PREVIOUS_RUNTIME_DIR" "$RUNTIME_DIR"; fi
    die "无法激活新运行副本"
  fi
}

migrate_data_once() {
  /bin/mkdir -p "$DATA_DIR"
  /bin/chmod 700 "$APP_SUPPORT_ROOT" "$DATA_DIR"
  if [ ! -f "$DATA_DIR/owlpath.db" ] && [ -f "$SOURCE_DATA_DIR/owlpath.db" ]; then
    say "首次迁移 OwlPath 数据（以后更新不会覆盖）……"
    /usr/bin/ditto "$SOURCE_DATA_DIR" "$DATA_DIR"
    /bin/chmod 600 "$DATA_DIR"/owlpath.db* 2>/dev/null || true
  fi
}

generate_plist() {
  local output_path="$1"
  /usr/bin/plutil -create xml1 "$output_path"
  /usr/bin/plutil -insert Label -string "$LABEL" "$output_path"
  /usr/bin/plutil -insert ProgramArguments -array "$output_path"
  /usr/bin/plutil -insert ProgramArguments.0 -string "$RUNTIME_PYTHON" "$output_path"
  /usr/bin/plutil -insert ProgramArguments.1 -string "-m" "$output_path"
  /usr/bin/plutil -insert ProgramArguments.2 -string "uvicorn" "$output_path"
  /usr/bin/plutil -insert ProgramArguments.3 -string "app.main:app" "$output_path"
  /usr/bin/plutil -insert ProgramArguments.4 -string "--app-dir" "$output_path"
  /usr/bin/plutil -insert ProgramArguments.5 -string "$RUNTIME_BACKEND" "$output_path"
  /usr/bin/plutil -insert ProgramArguments.6 -string "--host" "$output_path"
  /usr/bin/plutil -insert ProgramArguments.7 -string "127.0.0.1" "$output_path"
  /usr/bin/plutil -insert ProgramArguments.8 -string "--port" "$output_path"
  /usr/bin/plutil -insert ProgramArguments.9 -string "$PORT" "$output_path"
  /usr/bin/plutil -insert WorkingDirectory -string "$RUNTIME_DIR" "$output_path"
  /usr/bin/plutil -insert RunAtLoad -bool YES "$output_path"
  /usr/bin/plutil -insert KeepAlive -bool YES "$output_path"
  /usr/bin/plutil -insert ThrottleInterval -integer 5 "$output_path"
  /usr/bin/plutil -insert ExitTimeOut -integer 20 "$output_path"
  /usr/bin/plutil -insert StandardOutPath -string "$STDOUT_LOG" "$output_path"
  /usr/bin/plutil -insert StandardErrorPath -string "$STDERR_LOG" "$output_path"
  /usr/bin/plutil -insert EnvironmentVariables -dictionary "$output_path"
  /usr/bin/plutil -insert EnvironmentVariables.OWLPATH_DATA_DIR -string "$DATA_DIR" "$output_path"
  /usr/bin/plutil -insert EnvironmentVariables.PYTHONUNBUFFERED -string "1" "$output_path"
  /usr/bin/plutil -insert EnvironmentVariables.PATH -string "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" "$output_path"
  /usr/bin/plutil -lint "$output_path" >/dev/null
}

install_service() {
  require_macos
  stop_loaded_service
  assert_port_available
  deploy_runtime
  migrate_data_once
  /bin/mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

  local temporary_plist
  temporary_plist="$(/usr/bin/mktemp "${TMPDIR:-/tmp}/owlpath-launchagent.XXXXXX")"
  generate_plist "$temporary_plist"
  /usr/bin/install -m 0644 "$temporary_plist" "$PLIST_PATH"
  /bin/rm -f "$temporary_plist"

  /bin/launchctl enable "$SERVICE_TARGET"
  if ! /bin/launchctl bootstrap "$DOMAIN" "$PLIST_PATH"; then
    activation_failed "OwlPath LaunchAgent 无法加载" yes
  fi
  if ! wait_for_ready 80; then
    activation_failed "OwlPath 后台健康检查或网页检查未通过" yes
  fi
  finalize_runtime_deployment
  say "OwlPath 常驻服务已安装并启动。"
  say "网址：$URL"
  say "运行副本：$RUNTIME_DIR"
  say "持久数据：$DATA_DIR"
  say "日志：$LOG_DIR"
}

start_service() {
  require_macos
  [ -f "$PLIST_PATH" ] || die "服务尚未安装，请先运行：$0 install"
  [ -x "$RUNTIME_PYTHON" ] || die "运行副本不完整，请重新运行：$0 install"
  [ -f "$RUNTIME_FRONTEND_INDEX" ] || die "运行副本缺少网页，请重新运行：$0 install"
  /bin/launchctl enable "$SERVICE_TARGET"
  if ! is_loaded; then
    assert_port_available
    if ! /bin/launchctl bootstrap "$DOMAIN" "$PLIST_PATH"; then
      activation_failed "OwlPath LaunchAgent 无法加载" no
    fi
  elif ! ready_ok; then
    stop_loaded_service
    assert_port_available
    if ! /bin/launchctl bootstrap "$DOMAIN" "$PLIST_PATH"; then
      activation_failed "OwlPath LaunchAgent 无法重新加载" no
    fi
  fi
  if ! wait_for_ready 80; then
    activation_failed "OwlPath 启动后健康检查或网页检查未通过" no
  fi
}

restart_service() {
  require_macos
  [ -f "$PLIST_PATH" ] || die "服务尚未安装，请先运行：$0 install"
  stop_loaded_service
  assert_port_available
  /bin/launchctl enable "$SERVICE_TARGET"
  if ! /bin/launchctl bootstrap "$DOMAIN" "$PLIST_PATH"; then
    activation_failed "OwlPath LaunchAgent 无法重新加载" no
  fi
  if ! wait_for_ready 80; then
    activation_failed "OwlPath 重启后健康检查或网页检查未通过" no
  fi
  say "OwlPath 已重启（未重新构建前端）。"
}

status_service() {
  require_macos
  [ -f "$PLIST_PATH" ] || { say "安装状态：未安装"; return 1; }
  say "安装状态：已安装"
  say "配置：$PLIST_PATH"
  say "运行副本：$RUNTIME_DIR"
  say "持久数据：$DATA_DIR"
  if ! is_loaded; then say "launchd 状态：未加载"; return 1; fi

  local details state pid last_exit
  details="$(/bin/launchctl print "$SERVICE_TARGET")"
  state="$(printf '%s\n' "$details" | /usr/bin/awk '/^[[:space:]]*state = / {print $3; exit}')"
  pid="$(printf '%s\n' "$details" | /usr/bin/awk '/^[[:space:]]*pid = / {print $3; exit}')"
  last_exit="$(printf '%s\n' "$details" | /usr/bin/awk '/^[[:space:]]*last exit code = / {sub(/^.*= /, ""); print; exit}')"
  say "launchd 状态：${state:-loaded}"
  [ -z "$pid" ] || say "PID：$pid"
  [ -z "$last_exit" ] || say "上次退出码：$last_exit"
  if ready_ok; then
    say "健康检查：通过"
    say "网页检查：通过 ($URL)"
  else
    say "健康检查：失败"
    return 1
  fi
}

health_service() {
  local response
  response="$(/usr/bin/curl -fsS --max-time 5 "$URL/api/health")" || die "无法连接 $URL/api/health"
  if ! health_ok; then
    die "端口 $PORT 有 HTTP 响应，但不是预期的 $EXPECTED_SERVICE $EXPECTED_VERSION"
  fi
  frontend_ok || die "API 可用，但 OwlPath 网页未正确部署"
  listener_owned_by_service || die "端口监听进程不属于已安装的 OwlPath 服务"
  printf '%s\n' "$response"
}

open_service() {
  if [ ! -f "$PLIST_PATH" ]; then install_service; else start_service; fi
  /usr/bin/open "$URL/?ui=$(/bin/date +%s)"
  say "已打开：$URL"
}

update_service() {
  require_macos
  if [ ! -x "$SOURCE_VENV/bin/python" ] || [ ! -d "$PROJECT_DIR/frontend/node_modules" ]; then
    "$PROJECT_DIR/scripts/setup.sh"
  fi
  build_frontend
  install_service
  say "OwlPath 代码与网页已同步到后台运行副本，数据未被覆盖。"
}

logs_service() {
  local mode="${1:-recent}"
  if [ ! -f "$STDOUT_LOG" ] && [ ! -f "$STDERR_LOG" ]; then die "尚无服务日志"; fi
  if [ "$mode" = "--follow" ] || [ "$mode" = "-f" ]; then
    /usr/bin/tail -n 100 -F "$STDOUT_LOG" "$STDERR_LOG"
  else
    show_recent_logs
  fi
}

uninstall_service() {
  require_macos
  stop_loaded_service
  if [ -f "$PLIST_PATH" ]; then /bin/rm -f "$PLIST_PATH"; fi
  say "OwlPath 常驻服务已卸载。"
  say "运行副本、数据和日志已保留：$APP_SUPPORT_ROOT，$LOG_DIR"
}

usage() {
  printf '%s\n' \
    "用法：$0 <command>" "" \
    "  install       部署到 Application Support 并安装登录自启服务" \
    "  status        显示 launchd、PID、API 和网页健康状态" \
    "  health        校验 OwlPath 身份、监听进程和网页" \
    "  open          确保服务运行并打开网页" \
    "  restart       仅重启已部署运行副本" \
    "  update        构建前端，同步代码并重启，不覆盖数据" \
    "  logs [-f]     查看最近日志，-f 持续跟踪" \
    "  uninstall     停止并卸载自启服务，保留数据" \
    "  help          显示本帮助"
}

acquire_command_lock_if_needed() {
  local command="${1:-help}" status
  case "$command" in
    install|open|restart|update|uninstall) ;;
    *) return 0 ;;
  esac
  [ "${OWLPATH_SERVICE_LOCK_HELD:-0}" != "1" ] || return 0
  [ -x /usr/bin/lockf ] || die "未找到 macOS lockf，无法安全串行化服务管理"
  /bin/mkdir -p "$APP_SUPPORT_ROOT"
  set +e
  /usr/bin/lockf -t 60 "$LOCK_FILE" \
    /usr/bin/env OWLPATH_SERVICE_LOCK_HELD=1 "$SCRIPT_PATH" "$@"
  status=$?
  set -e
  exit "$status"
}

acquire_command_lock_if_needed "$@"

case "${1:-help}" in
  install) install_service ;;
  status) status_service ;;
  health) health_service ;;
  open) open_service ;;
  restart) restart_service ;;
  update) update_service ;;
  logs) logs_service "${2:-recent}" ;;
  uninstall) uninstall_service ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
