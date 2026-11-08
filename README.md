# 台股自選股評分追蹤程式

個人自用的台股選股與追蹤程式。完整規格見 [CLAUDE.md](CLAUDE.md)。

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
- [ ] 9. 個人網站
- [ ] 10. 回測
- [ ] 11. 排程與維運文件

## 已完成的前置設定

| 項目 | 狀態 |
|---|---|
| GitHub 私人 repo、CI | 完成 |
| Neon 資料庫（專案 tw-stock-tracker，round-violet-78597036） | 完成，migration 001、002 已套用 |
| GitHub Secrets：DATABASE_URL、FINMIND_TOKEN | 完成 |
| 證交所／櫃買資料欄位 probe | 完成，2026-11-06 全市場實際抓取通過（約 1,980 檔，26 秒） |
| 股票清單、產業族群、自訂族群 | 完成：1,990 家公司、35 個產業族群＋5 個自訂族群 |
| LINE Messaging API（LINE_CHANNEL_ACCESS_TOKEN、LINE_OWNER_USER_ID） | 待擁有者設定（未設定時報告只輸出到排程記錄，不會推播）；設定方式：`gh secret set LINE_CHANNEL_ACCESS_TOKEN`、`gh secret set LINE_OWNER_USER_ID` |
| Vercel 專案（root 設為 web/，環境變數 DATABASE_URL、SITE_PASSWORD、SESSION_SECRET） | 第 9 階段建立 |
| 自選股 | 擁有者在程式完成後自行加入 |

## 交易日誌（網站完成前用指令登錄）

```bash
python -m engine.cli capital 1500000                         # 設定總資金（影響建議張數）
python -m engine.cli trade buy 2330 1050 1000 --date 2026-11-06   # 代號 價格 股數（或 --lots 1）
python -m engine.cli trade sell 2330 1100 1000                # 手續費、證交稅自動計算，可用 --fee/--tax 覆寫
python -m engine.cli trade list                               # 交易紀錄與目前持股
python -m engine.cli stats                                    # 勝率、期望值、與加權指數及 0050 比較
```

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
2. 指示：「讀 CLAUDE.md、README.md、docs/open-questions.md，從第 14 節第 9 階段開始開發」
3. 消息面判讀目前用關鍵字（排程預設 `AI_PROVIDER=rules`）；要改用 Claude，在 GitHub Variables 設 `AI_PROVIDER=claude` 並加 `ANTHROPIC_API_KEY` Secret（見 docs/open-questions.md #13）
4. 第 6 階段（LINE）前，擁有者需在 GitHub Secrets 加上 `LINE_CHANNEL_ACCESS_TOKEN`、`LINE_OWNER_USER_ID`
5. 第 9 階段（網站）前，在 Vercel 匯入本 repo，Root Directory 設為 `web`，並設定 `DATABASE_URL`、`SITE_PASSWORD`、`SESSION_SECRET`
