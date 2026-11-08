#!/bin/zsh
# 停止並移除盤中停損監控（launchd）。
set -euo pipefail
PLIST="$HOME/Library/LaunchAgents/com.twstock.monitor.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "已移除盤中停損監控"
