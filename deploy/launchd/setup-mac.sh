#!/bin/zsh
# 一次完成 Mac 上的盤中停損監控安裝：虛擬環境、套件、.env、連線測試、launchd 常駐。
# 用法（終端機）：
#   git clone https://github.com/zengdayaozdy-sudo/tw-stock-tracker.git ~/tw-stock-tracker
#   ~/tw-stock-tracker/deploy/launchd/setup-mac.sh
# 可重複執行：已安裝的步驟會略過，修改 .env 後重跑即重新啟動監控。
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

step() { print -P "\n%F{cyan}▸ $1%f"; }

if [[ -d .git ]] && xcode-select -p >/dev/null 2>&1; then
  step "更新程式碼"
  git pull --ff-only || print "（略過：本機有修改或沒有網路，沿用目前版本）"
fi

step "尋找 Python 3.12 以上"
PY=""
for c in python3.13 python3.12 python3; do
  if command -v $c >/dev/null && $c -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    PY=$(command -v $c); break
  fi
done
if [[ -z "$PY" ]]; then
  if command -v brew >/dev/null; then
    brew install python@3.12
    PY="$(brew --prefix python@3.12)/bin/python3.12"
  else
    print "找不到 Python 3.12 以上，請先到 https://www.python.org/downloads/macos/ 安裝後重跑"; exit 1
  fi
fi
print "使用 $PY（$($PY --version)）"

step "建立虛擬環境與安裝套件"
[[ -x .venv/bin/python ]] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

step "設定 .env（輸入時不會顯示在畫面上）"
[[ -f .env ]] || cp .env.example .env
chmod 600 .env
ask() {
  local key=$1 label=$2 current
  current=$(grep -E "^$key=" .env | head -1 | cut -d= -f2- || true)
  if [[ -n "$current" ]]; then print "$label：已設定"; return; fi
  local value=""
  while [[ -z "$value" ]]; do read -rs "value?$label：" ; print; done
  KEY="$key" VALUE="$value" .venv/bin/python - <<'PYEOF'
import os, re
from pathlib import Path
p = Path(".env"); key, value = os.environ["KEY"], os.environ["VALUE"].strip()
text = p.read_text(encoding="utf-8")
line = f"{key}={value}"
text = re.sub(rf"^{key}=.*$", lambda _m: line, text, flags=re.M) if re.search(rf"^{key}=", text, re.M) else text + f"\n{line}\n"
p.write_text(text, encoding="utf-8")
PYEOF
}
ask DATABASE_URL "Neon 資料庫連線字串（DATABASE_URL）"
ask LINE_CHANNEL_ACCESS_TOKEN "LINE Channel access token"
ask LINE_OWNER_USER_ID "LINE 推播對象 user ID（U 開頭）"

step "連線測試（檢查一次持股現價，不在交易時間會直接略過）"
.venv/bin/python -m engine.cli monitor --once

step "安裝為登入後自動啟動的背景程式"
./deploy/launchd/install.sh
sleep 3
launchctl print "gui/$(id -u)/com.twstock.monitor" | grep -E "^\s*state" || true
print "\n完成。交易日 09:00–13:30 盤中請讓 Mac 接上電源、不要闔上螢幕。"
