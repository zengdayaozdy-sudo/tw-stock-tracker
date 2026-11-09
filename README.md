# 台股自選股評分追蹤程式

個人自用的台股選股與追蹤程式。完整規格見 [CLAUDE.md](CLAUDE.md)，安裝、補跑、常見錯誤見 [docs/runbook.md](docs/runbook.md)。

## 快速開始

```bash
cp .env.example .env          # 填入各項金鑰
pip install -e ".[dev]"
python -m engine.cli plan     # 檢視目前方案與鎖定功能
python -m engine.cli migrate  # 建立資料表（需 DATABASE_URL）
pytest -q
```

每日流程（每一步都可加 `--date YYYY-MM-DD` 補跑）：

```bash
python -m engine.cli fetch        # 抓資料＋完整性檢查
python -m engine.cli liquidity    # 流動性過濾
python -m engine.cli news         # 消息面排雷（AI 判讀，連不上時改用關鍵字）
python -m engine.cli score        # 三面向評分
python -m engine.cli filters      # 大盤燈號、族群強弱、事件行事曆
python -m engine.cli signals      # 買進訊號、參考價位、建議張數（資料不完整時暫停）
python -m engine.cli holdings     # 持股檢查：停損、移動停利回落、分數跌出門檻、持有過久
python -m engine.cli notify       # LINE 每日報告＋系統警告（同一天只送一次，--resend 重送）
python -m engine.cli alert        # 只檢查並推播系統警告（排程任一步驟失敗時自動執行）
```

## 開發進度

- [x] 1. 專案骨架、設定檔、方案開關、資料庫結構
- [x] 2. 資料來源、額度管理、完整性檢查
- [x] 3. 評分引擎
- [x] 4. 濾網（流動性、消息面排雷、大盤燈號、族群強弱、事件行事曆）
- [x] 5. 訊號與風控（入選名單、進場條件、參考價位、風控張數）
- [x] 6. LINE 每日報告與系統警告
- [x] 7. 持股追蹤、賣出提醒、交易日誌統計
- [x] 8. 盤中停損監控（常駐在擁有者的 Mac）
- [x] 9. 個人網站（Next.js，密碼保護）
- [x] 10. 回測（backtrader，調參／保留驗證區間）
- [x] 11. 排程與維運文件（[docs/runbook.md](docs/runbook.md)）

## 已完成的前置設定

| 項目 | 狀態 |
|---|---|
| GitHub 私人 repo、CI | 完成 |
| Neon 資料庫（專案 tw-stock-tracker，round-violet-78597036） | 完成，migration 001、002 已套用 |
| GitHub Secrets：DATABASE_URL、FINMIND_TOKEN | 完成 |
| 證交所／櫃買資料欄位 probe | 完成，2026-11-06 全市場實際抓取通過（約 1,980 檔，26 秒） |
| 股票清單、產業族群、自訂族群 | 完成：1,990 家公司、35 個產業族群＋5 個自訂族群 |
| LINE Messaging API（LINE_CHANNEL_ACCESS_TOKEN、LINE_OWNER_USER_ID） | 待擁有者設定（未設定時報告只輸出到排程記錄，不會推播）；設定方式：`gh secret set LINE_CHANNEL_ACCESS_TOKEN`、`gh secret set LINE_OWNER_USER_ID` |
| Vercel 專案（root 設為 web/，環境變數 DATABASE_URL、SITE_PASSWORD、SESSION_SECRET） | 程式已完成，待擁有者在 Vercel 匯入（見「個人網站」） |
| 自選股 | 擁有者在程式完成後自行加入 |

## 個人網站（web/）

Next.js 16（App Router）＋TypeScript，部署在 Vercel，單一密碼登入（HTTP-only cookie），未登入時所有頁面與 API 都無法存取。

| 頁面 | 內容 |
|---|---|
| 總覽 `/` | 大盤燈號與各輸入依據、今日買進訊號、族群強弱、持股與賣出提醒、系統狀態 |
| 族群 `/groups` | 族群排名（相對大盤報酬、平均分數、弱勢）與成分股排行 |
| 個股 `/stocks/代號` | K 線＋均線＋成交量＋訊號標記、評分拆解、參考價位、排雷紀錄、事件（可登錄法說會）、財報狗與 Goodinfo 連結 |
| 自選股 `/watchlist` | 新增／移除自選股（下一次排程自動回補歷史）、維護自訂族群 |
| 持股與交易 `/portfolio` | 登錄買賣（自動計算費用、記錄當時分數與訊號）、持股、勝率與期望值、與加權指數及 0050 比較 |
| 設定 `/settings` | 總資金、通知開關、方案功能（鎖頭標示升級後可用） |
| 系統狀態 `/status` | 排程紀錄、資料完整性、FinMind 額度、盤中監控心跳 |

**Vercel 設定**：Import 本 repo → Root Directory 設為 `web` → 環境變數：

| 名稱 | 說明 |
|---|---|
| `DATABASE_URL` | Neon 連線字串（建議用 pooled 連線） |
| `SITE_PASSWORD` | 網站登入密碼 |
| `SESSION_SECRET` | 至少 16 字元的隨機字串，例如 `openssl rand -hex 32` 的輸出 |
| `MCP_TOKEN` | 選用。MCP 接口的存取權杖，至少 32 字元，例如 `openssl rand -hex 32` 的輸出；未設定時 MCP 接口關閉 |

推送到 `main` 即自動部署。方案、費用與風控規則由每日排程的 `sync-config` 寫入資料庫，網站與引擎使用同一份設定。

本機開發：`cd web && npm install && npm run dev`（`web/.env.local` 放上述三個變數）。

## MCP 接口（讓 Claude 直接讀取與操作）

網站提供 MCP（Model Context Protocol）接口，Claude 可以直接查詢評分、訊號、持股，並維護自選股、族群與交易日誌。支援 MCP 2026-07-28（無狀態）與 2025 年版協定。**不提供任何下單功能。**

| 連線方式 | 網址 | 驗證 | 適用 |
|---|---|---|---|
| 標頭 | `https://<網站>/api/mcp` | `Authorization: Bearer <MCP_TOKEN>` | Claude Code、Claude Desktop |
| 路徑 | `https://<網站>/api/mcp/<MCP_TOKEN>` | 網址本身 | claude.ai 自訂連接器（無法自訂標頭） |

設定：Vercel 環境變數加上 `MCP_TOKEN`（`openssl rand -hex 32`）→ Redeploy。

```bash
# Claude Code
claude mcp add --transport http tw-stock https://<網站>/api/mcp --header "Authorization: Bearer <MCP_TOKEN>"
```

claude.ai：設定 → 連接器 → 新增自訂連接器 → 網址填 `https://<網站>/api/mcp/<MCP_TOKEN>`，驗證留空。路徑方式的網址等同密碼，不要分享；外流時換一組 `MCP_TOKEN` 並 Redeploy，舊網址立即失效。

| 工具 | 用途 |
|---|---|
| `get_overview` | 當日大盤燈號、族群強弱、買進訊號、賣出提醒、持股、系統狀態 |
| `search_stocks` | 以代號或名稱搜尋股票 |
| `get_stock` | 個股評分拆解、訊號與參考價位、排雷紀錄、事件、近期股價 |
| `get_price_history` | 日 K 歷史 |
| `list_signals` | 指定日的買進訊號與賣出提醒 |
| `get_watchlist` | 自選股與最新分數 |
| `list_groups`、`get_group` | 族群強弱排名、成分股排行 |
| `get_portfolio` | 持股損益、勝率與期望值、最近交易 |
| `get_system_status` | 方案、排程紀錄、FinMind 用量、盤中監控心跳 |
| `add_to_watchlist`、`remove_from_watchlist` | 維護自選股 |
| `create_custom_group`、`add_group_members`、`remove_group_member` | 維護自訂族群 |
| `record_trade` | 登錄已在券商成交的買賣（只記帳，不下單） |
| `update_holding_levels` | 修改持股停損停利價 |

寫入工具與網站表單共用 `web/lib/mutations.ts`，結果完全一致。

## 交易日誌（也可用指令登錄）

```bash
python -m engine.cli capital 1500000                         # 設定總資金（影響建議張數）
python -m engine.cli trade buy 2330 1050 1000 --date 2026-11-06   # 代號 價格 股數（或 --lots 1）
python -m engine.cli trade sell 2330 1100 1000                # 手續費、證交稅自動計算，可用 --fee/--tax 覆寫
python -m engine.cli trade list                               # 交易紀錄與目前持股
python -m engine.cli stats                                    # 勝率、期望值、與加權指數及 0050 比較
```

## 回測

沿用正式系統的評分、濾網、進場條件、參考價位、風控與賣出規則，逐日只用當天以前已知的資料（財報依公布日），交易由 backtrader 模擬：D 日收盤產生訊號、D+1 日成交，扣除手續費與證交稅，股價已還原除權息與分割。

```bash
python -m engine.cli backtest-data --years 5                    # 抓大盤長期歷史、除權息參考價
python -m engine.cli backtest                                   # 調參區間（可重複執行）
python -m engine.cli backtest --set risk.stop_loss.percent=0.06 # 覆寫參數試算
python -m engine.cli backtest --holdout                         # 保留驗證區間：調參完成後只執行一次
```

- 期間在 `config/backtest.yaml`：起日、最後幾個月為保留區間（預設 12 個月）、初始資金
- 報告：年化報酬、最大回撤、勝率、平均獲利／虧損、期望值、交易次數，與加權指數及 0050（含息）比較；寫入 `reports/` 與資料表 `backtest_runs`
- 相同參數與資料會得到相同結果（報告附指紋）
- 免費模式只回測自選股，報告會標註選擇偏差；消息面不參與回測
- 也可從 GitHub Actions「手動操作」執行 `backtest-data`／`backtest`／`backtest-holdout`，報告在執行結果的 Artifacts

## 盤中停損監控（這台 Mac）

交易日 09:00–13:30 每 3 分鐘檢查持股現價（證交所即時行情），觸及停損就推播 LINE，同一檔同一天只提醒一次。

```bash
cp .env.example .env                 # 填入 DATABASE_URL、LINE_CHANNEL_ACCESS_TOKEN、LINE_OWNER_USER_ID
python -m engine.cli monitor --once  # 先手動檢查一次確認設定正確
./deploy/launchd/install.sh          # 安裝為登入後自動啟動、當掉自動重啟
./deploy/launchd/uninstall.sh        # 移除
```

- 記錄檔：`~/Library/Logs/tw-stock-tracker/monitor.log`
- 交易時間內會用 `caffeinate` 阻止系統閒置睡眠；闔上筆電螢幕（未接電源）仍會睡眠
- 程式停止時，收盤後批次會依心跳紀錄推播「盤中停損監控可能已停止」
- Linux 用 `deploy/systemd/tw-stock-monitor.service`，Docker 用 `deploy/docker/Dockerfile`

## 手動操作（GitHub → Actions → 手動操作 → Run workflow）

- `watch-add` 參數填股票代號（空白分隔）加入自選股；`backfill` 回補自選股 3 年歷史
- `companies` 更新公司資料與股數；`seed-groups` 依 config/groups.yaml 建立族群
- `trade-buy`／`trade-sell` 參數填「代號 價格 股數」（可加 `--date YYYY-MM-DD`）；`trade-list`、`stats` 查看；`capital` 填總資金
- 每日收盤後批次可指定日期補跑

## 交給 Claude Code 接手

1. 用 Claude Code 開啟本 repo
2. 第 14 節的 11 個階段已全部完成；後續調整先讀 CLAUDE.md、README.md、docs/runbook.md、docs/open-questions.md、docs/decisions.md
3. 消息面判讀目前用關鍵字（排程預設 `AI_PROVIDER=rules`）；要改用 Claude，在 GitHub Variables 設 `AI_PROVIDER=claude` 並加 `ANTHROPIC_API_KEY` Secret（見 docs/open-questions.md #13）
4. 第 6 階段（LINE）前，擁有者需在 GitHub Secrets 加上 `LINE_CHANNEL_ACCESS_TOKEN`、`LINE_OWNER_USER_ID`
5. 第 9 階段（網站）前，在 Vercel 匯入本 repo，Root Directory 設為 `web`，並設定 `DATABASE_URL`、`SITE_PASSWORD`、`SESSION_SECRET`
