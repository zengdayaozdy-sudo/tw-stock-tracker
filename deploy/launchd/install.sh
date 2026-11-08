#!/bin/zsh
# 安裝盤中停損監控為 macOS 登入後自動啟動的背景程式（launchd）。
# 用法：在專案根目錄執行 ./deploy/launchd/install.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
LOGDIR="$HOME/Library/Logs/tw-stock-tracker"
PLIST="$HOME/Library/LaunchAgents/com.twstock.monitor.plist"

[[ -x "$PYTHON" ]] || { echo "找不到 $PYTHON，請先建立虛擬環境：python3 -m venv .venv && .venv/bin/pip install -e ."; exit 1; }
[[ -f "$REPO/.env" ]] || { echo "找不到 $REPO/.env，請先由 .env.example 複製並填入 DATABASE_URL 與 LINE 金鑰"; exit 1; }
grep -q '^DATABASE_URL=.\+' "$REPO/.env" || { echo ".env 的 DATABASE_URL 尚未填寫"; exit 1; }

mkdir -p "$LOGDIR" "$HOME/Library/LaunchAgents"
sed -e "s|__PYTHON__|$PYTHON|" -e "s|__REPO__|$REPO|" -e "s|__LOGDIR__|$LOGDIR|" \
  "$REPO/deploy/launchd/com.twstock.monitor.plist.template" > "$PLIST"

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "已安裝並啟動：$PLIST"
echo "記錄檔：$LOGDIR/monitor.log"
echo "狀態：launchctl print gui/$(id -u)/com.twstock.monitor | grep state"
