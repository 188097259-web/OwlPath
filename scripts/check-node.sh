#!/usr/bin/env bash

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "未找到 Node.js/npm。请安装 Node.js 22.18 或更高版本。" >&2
  exit 1
fi

NODE_VERSION_OK="$(node -p 'const [a,b]=process.versions.node.split(".").map(Number); Number(a>22 || (a===22 && b>=18))')"
if [ "$NODE_VERSION_OK" != "1" ]; then
  echo "当前 Node.js 为 $(node --version)；OwlPath 需要 Node.js 22.18 或更高版本。" >&2
  exit 1
fi
