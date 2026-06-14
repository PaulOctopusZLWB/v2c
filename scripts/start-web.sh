#!/usr/bin/env bash
# Start the Personal Context Node local web control panel (foreground).
#   ./scripts/start-web.sh                      # uses the acceptance test config (real funasr + safe .tmp vault)
#   ./scripts/start-web.sh config/local.toml    # use your own config
# Open http://127.0.0.1:8765/app/  ·  Ctrl-C to stop.
set -euo pipefail
cd "$(dirname "$0")/.."
CONFIG="${1:-.tmp/acceptance/config.toml}"

# Free the port if something is already bound (e.g. a previous run).
if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port 8765 is in use — stopping the previous web server first."
  pkill -f "pcn web" 2>/dev/null || true
  sleep 1
fi

echo "──────────────────────────────────────────────"
echo " Personal Context Node — 本机控制台"
echo " 打开:  http://127.0.0.1:8765/app/"
echo " 配置:  $CONFIG"
echo " 停止:  Ctrl-C"
echo "──────────────────────────────────────────────"
UV_CACHE_DIR=.tmp/uv-cache exec uv run pcn web --config "$CONFIG" --host 127.0.0.1 --port 8765
