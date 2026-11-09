# 維運手冊

台股自選股評分追蹤程式的安裝、設定、日常運作、補跑與常見錯誤處理。開發規格見 [CLAUDE.md](../CLAUDE.md)，技術決策見 [decisions.md](decisions.md)，待決事項見 [open-questions.md](open-questions.md)。

## 1. 系統組成

| 元件 | 跑在哪裡 | 做什麼 |
|---|---|---|
| 收盤後批次（`engine/`） | GitHub Actions `每日收盤後批次`（週一至週五 15:30） | 抓資料 → 完整性檢查 → 流動性 → 排雷 → 評分 → 燈號／族群／事件 → 訊號 → 持股檢查 → LINE 報告 |
| 每週維護 | GitHub Actions `每週維護`（週六 12:00） | 公司資料與股數、族群、集保股權分散表、清理舊紀錄 |
| 盤中停損監控 | 擁有者的 Mac（launchd 常駐） | 交易日 09:00–13:30 每 3 分鐘檢查持股，觸及停損推播；17:30 檢查收盤後批次是否有執行 |
| 個人網站（`web/`） | Vercel | 密碼保護的儀表板、族群、個股、自選股、持股與交易、設定、系統狀態 |
| 資料庫 | Neon（PostgreSQL） | 所有資料與排程紀錄（`job_logs`） |
| 手動操作 | GitHub Actions `手動操作` | 自選股、回補、登錄交易、總資金、回測 |

所有日期以台灣時間（Asia/Taipei）處理。GitHub 排程以 UTC 撰寫：15:30 台灣時間 ＝ `30 7 * * 1-5`；週六 12:00 ＝ `0 4 * * 6`。

## 2. 安裝

### 2.1 本機（開發、指令操作、盤中監控）

```bash
git clone https://github.com/zengdayaozdy-sudo/tw-stock-tracker.git
cd tw-stock-tracker
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,claude,backtest]"
cp .env.example .env          # 填入下一節的環境變數
.venv/bin/python -m engine.cli plan
.venv/bin/python -m engine.cli migrate
```

本機指令會自動讀取專案根目錄的 `.env`（已存在的環境變數不覆寫）。`.env` 不會被 commit。

### 2.2 GitHub（排程）

Settings → Secrets and variables → Actions：

| 類型 | 名稱 | 必要 | 說明 |
|---|---|---|---|
| Secret | `DATABASE_URL` | ✓ | Neon 連線字串 |
| Secret | `FINMIND_TOKEN` | ✓ | FinMind token（有 token 每小時 600 次） |
| Secret | `LINE_CHANNEL_ACCESS_TOKEN` | ✓ | LINE Messaging API 長期 token |
| Secret | `LINE_OWNER_USER_ID` | ✓ | 推播對象（自己的 user ID，U 開頭） |
| Secret | `ANTHROPIC_API_KEY` | | 消息面改用 Claude 判讀時 |
| Variable | `PLAN` | | `free`（預設）／`sponsor`／`pro` |
| Variable | `AI_PROVIDER` | | `rules`（預設，關鍵字）／`claude`／`ollama` |
| Variable | `SITE_URL` | | 網站網址，LINE 報告附連結 |

指令列設定（值會提示輸入、不顯示在畫面）：

```bash
gh secret set LINE_CHANNEL_ACCESS_TOKEN
gh secret set LINE_OWNER_USER_ID
gh variable set PLAN --body free
```

**LINE 取得方式**：LINE Official Account Manager 建立官方帳號 → 啟用 Messaging API → LINE Developers Console 的 channel：Basic settings 最下方 **Your user ID**、Messaging API 頁 **Channel access token (long-lived)**；用手機掃同頁 QR code 加官方帳號為好友。

### 2.3 Vercel（網站）

Import 本 repo → Root Directory 設為 `web` → 環境變數：

| 名稱 | 說明 |
|---|---|
| `DATABASE_URL` | Neon 連線字串（建議 pooled） |
| `SITE_PASSWORD` | 登入密碼 |
| `SESSION_SECRET` | 至少 16 字元的隨機字串，例如 `openssl rand -hex 32` |
| `MCP_TOKEN` | 選用，MCP 接口權杖，至少 32 字元；未設定時接口關閉（見 README「MCP 接口」） |

推送到 `main` 自動部署。網站的方案、功能鎖頭、費用與風控規則由每日排程的 `sync-config` 寫入資料庫，第一次每日排程執行後才會顯示。

### 2.4 盤中停損監控（Mac）

```bash
.venv/bin/python -m engine.cli monitor --once   # 確認 .env 的 DATABASE_URL、LINE 設定正確
./deploy/launchd/install.sh                     # 安裝為登入後自動啟動、當掉自動重啟
```

- 記錄檔：`~/Library/Logs/tw-stock-tracker/monitor.log`
- 狀態：`launchctl print gui/$(id -u)/com.twstock.monitor | grep state`
- 移除：`./deploy/launchd/uninstall.sh`；修改 `.env` 後重新執行 `install.sh` 即重啟
- 交易時間內會用 `caffeinate` 防止閒置睡眠；**闔上筆電螢幕且未接電源仍會睡眠**，盤中請接電源
- Linux：`deploy/systemd/tw-stock-monitor.service`；Docker：`deploy/docker/Dockerfile`

### 2.5 第一次啟用

1. `migrate` → `companies` → `seed-groups`（或 GitHub Actions 手動操作執行 `companies`、`seed-groups`）
2. 網站「自選股」或 `watch add 2330 2317 …` 加入自選股（免費模式上限 100 檔）
3. 回補歷史：每日排程的 `backfill-new` 每天自動補最多 10 檔；想一次補完可手動 `backfill --years 5`
4. 網站「設定」輸入總資金（影響建議張數）
5. 回測前：`backtest-data --years 5`

## 3. 環境變數一覽

| 名稱 | 用在 | 說明 |
|---|---|---|
| `PLAN` | 引擎 | free／sponsor／pro，改了即解鎖對應功能，不需改程式 |
| `DATABASE_URL` | 全部 | Neon 連線字串 |
| `FINMIND_TOKEN` | 引擎 | FinMind API token |
| `LINE_CHANNEL_ACCESS_TOKEN`、`LINE_OWNER_USER_ID` | 引擎、盤中監控 | 未設定時只輸出內容、不推播（不算已送出，設定後重跑可補送） |
| `AI_PROVIDER` | 引擎 | rules／claude／ollama；AI 連不上時自動改用關鍵字 |
| `OLLAMA_BASE_URL`、`OLLAMA_MODEL` | 引擎 | Ollama 設定 |
| `ANTHROPIC_API_KEY`、`ANTHROPIC_MODEL` | 引擎 | Claude（預設 claude-opus-5-5） |
| `SITE_PASSWORD`、`SESSION_SECRET` | 網站 | 單一密碼登入與 session 簽章 |
| `MCP_TOKEN` | 網站 | MCP 接口權杖（至少 32 字元）；未設定或太短時接口關閉 |
| `SITE_URL` | 引擎 | LINE 報告附網站連結 |
| `TEST_DATABASE_URL` | 測試 | 測試用資料庫（會清空 public schema） |

## 4. 日常運作

### 4.1 每日時間線（交易日）

| 時間 | 事件 |
|---|---|
| 09:00–13:30 | Mac 盤中監控每 3 分鐘檢查持股停損 |
| 15:30 起 | GitHub 排程啟動（可能延遲數十分鐘）；官方資料未更新時最多再等 90 分鐘 |
| 約 16:00–17:00 | LINE 每日報告；網站更新 |
| 17:30 | Mac 檢查當天收盤後批次是否已開始，沒有就推播「排程未執行」 |

休市日：`trading-day` 判斷後略過所有步驟，並留下休市紀錄。

### 4.2 指令一覽（`python -m engine.cli <指令>`，大多可加 `--date YYYY-MM-DD`）

| 指令 | 用途 |
|---|---|
| `plan` | 目前方案與鎖定功能 |
| `migrate` | 套用資料庫 migration |
| `sync-config` | 方案與風控設定寫入資料庫（網站用） |
| `trading-day` | 是否為交易日 |
| `fetch [--wait-minutes N]` | 抓當日資料＋完整性檢查（未通過結束碼 2） |
| `backfill [--years N] [代號…]` | 回補歷史（預設全部自選股 3 年） |
| `backfill-new` | 自動回補歷史不足的自選股與持股（每次最多 10 檔） |
| `liquidity` / `news` / `score` / `filters` | 濾網與評分各步驟 |
| `signals [--force]` | 買進訊號；資料不完整時暫停（`--force` 僅供除錯） |
| `holdings` | 持股檢查與賣出提醒 |
| `notify [--resend]` / `alert [--message …]` | LINE 每日報告／系統警告 |
| `watch add/remove/list` | 自選股 |
| `trade buy/sell/list`、`stats`、`capital` | 交易日誌、統計、總資金 |
| `companies` / `seed-groups` / `holders` / `maintenance` | 每週維護 |
| `monitor [--once]` | 盤中停損監控 |
| `backtest [--set …] [--holdout]`、`backtest-data` | 回測 |
| `probe` | 實際呼叫證交所／櫃買端點，檢查欄位名稱是否異動 |

## 5. 補跑

**整天補跑**：GitHub → Actions → `每日收盤後批次` → Run workflow → 填日期。補跑時 `fetch` 不等待；各步驟都是 upsert／覆寫當日結果，重跑不會重複。

**單一步驟**：例如消息面判讀後要更新排除名單與訊號：

```bash
python -m engine.cli news --date 2026-11-06
python -m engine.cli filters --date 2026-11-06 --no-refresh
python -m engine.cli signals --date 2026-11-06
python -m engine.cli notify --date 2026-11-06 --resend
```

**注意**：證交所／櫃買的每日行情 API 只提供「最新一天」，`fetch` 無法補抓過去日期的全市場行情；過去日期的自選股資料請用 `backfill`（FinMind）。補跑過去日期主要用於重算評分、濾網、訊號。

## 6. 常見錯誤

| 症狀 | 原因 | 處理 |
|---|---|---|
| LINE 收到「資料完整性檢查未通過，今日暫停訊號」 | 官方資料筆數不足、自選股缺價、日期不符 | 系統狀態頁看錯誤明細；通常是官方資料延遲，稍後手動重跑每日排程 |
| 「官方資料尚未更新」、當天沒有報告 | 證交所 15:30 後仍未更新且等了 90 分鐘 | 手動重跑每日排程（不填日期） |
| 「排程未執行」 | GitHub 排程大幅延遲、Actions 故障，或 workflow 被停用（公開 repo 60 天無活動會自動停用；本 repo 為私人，但仍可能被手動停用） | Actions 頁面確認 workflow 為 Enabled，並手動執行一次 |
| 「FinMind 額度用盡」 | 一次回補太多檔 | 程式會暫停到下一個整點後繼續；減少 `backfill-new --limit` 或分天回補 |
| 「盤中停損監控可能已停止」 | Mac 睡眠、關機或程式錯誤 | 看 `monitor.log`；`launchctl kickstart -k gui/$(id -u)/com.twstock.monitor` 重啟 |
| 報告只出現在排程記錄、手機沒收到 | 未設定 LINE Secrets 或未加官方帳號好友 | 設定 Secrets 後 `notify --resend` |
| LINE 推播 HTTP 401／403 | token 失效或被重新發行 | 重新發行長期 token 並更新 Secret |
| LINE 推播 HTTP 429 | 超過每月訊息額度 | LINE Official Account Manager 查看方案額度；可調小 `notify.yaml` 的 `max_messages_per_report` |
| 評分顯示「資料不足」 | 股價歷史少於 240 天 | 等 `backfill-new` 或手動 `backfill` |
| 評分拆解大量「資料缺漏，以中性分計」 | 財報欄位名稱不符（open-questions #11）或尚未回補 | 檢查 `financials.metrics` 的鍵名，必要時調整 `engine/scoring/indicators.py` 的 `METRIC_KEYS` |
| 證交所／櫃買欄位解析失敗 | 官方欄位改名 | `python -m engine.cli probe` 看實際欄位，更新 `engine/sources/exchanges.py` 的候選名稱 |
| 網站登入頁顯示「尚未設定 SITE_PASSWORD 或 SESSION_SECRET」 | Vercel 環境變數未設或 SESSION_SECRET 少於 16 字元 | 補上後 Redeploy |
| 網站「讀取資料時發生錯誤」 | DATABASE_URL 錯誤或 Neon 暫停中 | Vercel 環境變數、Neon 主控台確認 |
| MCP 連線回 401（標頭方式）或 404（路徑方式） | `MCP_TOKEN` 未設定、少於 32 字元、或與連線設定不符 | Vercel 環境變數確認後 Redeploy；換權杖後客戶端設定也要更新 |
| MCP 工具回「資料庫還沒有評分資料」 | 每日排程尚未產生評分（新加入的自選股要等下一次排程回補） | 正常；等下一個交易日排程後再查 |
| `backtest` 回「保留驗證區間已驗證過」 | 保留區間只能跑一次 | 正常保護；確實要重跑加 `--force`（結果不再是乾淨的驗證） |
| `migrate` 失敗 | 連線字串錯誤或權限不足 | 確認 `DATABASE_URL`；migration 檔依序套用且可重複執行 |

## 7. 方案升級

1. FinMind 升級後，在 GitHub Variables 把 `PLAN` 改為 `sponsor` 或 `pro`
2. 下一次每日排程起：全市場掃描、全市場百分位、前 5% 名單、全市場族群強弱、全市場回測解鎖；網站鎖頭消失
3. 分點、即時報價屬於哪個方案仍待確認（open-questions #1、#2），確認後在 `config/plans.yaml` 調整
4. 全市場模式每日資料量較大，留意 Neon 免費方案容量（`maintenance` 每週清理舊紀錄）

## 8. 備份與資料

- Neon 主控台可建立 branch（資料庫快照）；大改動前先建 branch
- 手動備份：`pg_dump "$DATABASE_URL" -Fc -f backup.dump`；還原：`pg_restore -d "$DATABASE_URL" --clean backup.dump`
- `maintenance` 預設保留排程紀錄 180 天、新聞 360 天、心跳 30 天、API 用量 90 天

## 9. 驗收清單（第 16 節）

| # | 條件 | 自動測試 | 上線後確認 |
|---|---|---|---|
| 1 | free 模式 30 檔跑完一天未超出 FinMind 免費額度 | `test_acceptance.py`：最壞情況 316 次 ≤ 每小時 540 | 系統狀態頁 FinMind 額度 |
| 2 | 切換 sponsor 後鎖頭解鎖且不需改程式 | `test_acceptance.py`、`test_config.py` | 改 `PLAN` 後看設定頁 |
| 3 | 資料缺漏時暫停訊號並推播警告 | `test_signals_db.py`、`test_notify_db.py` | — |
| 4 | LINE 報告與網站一致 | `test_notify_db.py`（報告只讀資料庫，網站讀同一份資料） | 對照當天報告與總覽頁 |
| 5 | 網站未登入無法存取頁面與 API | `web/lib/proxy.test.ts` | 無痕視窗開網站 |
| 6 | 回測可重現 | `test_backtest.py`、`test_backtest_db.py`（相同參數相同指紋與結果） | 報告附指紋 |

測試：`pytest -q`（資料庫測試需 `TEST_DATABASE_URL` 或本機 PostgreSQL）、`cd web && npm test && npm run typecheck`。CI 在每次推送時執行。
