# 待確認事項

開發時查不到或需要擁有者決定的事項記在這裡。

| # | 問題 | 狀態 |
|---|---|---|
| 1 | 券商分點 `TaiwanStockTradingDailyReport`：官方文件標示單檔查詢需 Sponsor，全市場一次查詢需 Sponsor Pro | 已確認（2026-11-07 FinMind Chip 文件），plans.yaml 目前只放在 pro，若只需自選股可改放 sponsor |
| 2 | FinMind 即時報價屬於哪個方案、端點為何 | 待確認；確認前付費模式盤中也使用證交所即時行情 |
| 3 | FinMind 額度：有 token 每小時 600 次、無 token 300 次；付費方案上限待確認 | 免費額度已確認，sources.yaml 付費上限暫填 6000 |
| 4 | 股權分散表 `TaiwanStockHoldingSharesPer` 需付費；免費模式改用集保公開資料 | 已實作（2026-11-09 實測集保 CSV，每週六 holders 更新；歷史需累積數月後大戶指標才會脫離中性分） |
| 5 | 全市場不帶 data_id 的一次查詢（法人、融資券、外資持股）需 backer／sponsor | 已確認，免費模式改用證交所／櫃買公開資料 |
| 6 | 證交所／櫃買 OpenAPI 端點與欄位名稱 | 已確認：2026-11-06 實際抓取，上市 1,083 檔、上櫃 802 檔三大法人與融資券欄位皆正確 |
| 7 | 產業別是代碼 | 已處理：config/industries.yaml 對照表，查無代碼時顯示代碼 |
| 8 | 費城半導體、台積電 ADR、台指期未平倉的免費資料來源 | 已確認（2026-11-08 實測）：FinMind USStockPrice（^SOX、TSM）、TaiwanExchangeRate（USD）、TaiwanFuturesInstitutionalInvestors（TX），不帶 token 也可取得 |
| 9 | 回測採用 FinLab 或開源套件 | 已決定（2026-11-09）：backtrader，理由見 decisions.md |
| 10 | 盤中監控常駐程式要跑在哪台電腦（作業系統） | 已決定（2026-11-08）：擁有者的 Mac，以 launchd 啟動（deploy/launchd/install.sh） |
| 11 | FinMind 財報 type 名稱（EPS、GrossProfit、OperatingIncome、IncomeAfterTaxes、Equity、Liabilities、TotalAssets）需實測確認，名稱寫在 engine/scoring/indicators.py 的 METRIC_KEYS | 已解決（2026-11-09）：回補後評分拆解的基本面指標皆有值 |
| 12 | 免費模式市值 | 已處理：公司資料實收資本額 ÷ 面額 = 股數，每日 × 收盤價更新市值 |
| 13 | 消息面 AI 判讀在 GitHub Actions 上連不到本機 Ollama。選項：(a) 設 `AI_PROVIDER=claude` 並加 `ANTHROPIC_API_KEY` Secret；(b) 在自己電腦用 Ollama 補跑 `python -m engine.cli news`（之後再跑 `filters` 更新排除名單）；(c) 維持關鍵字判讀 | 已決定（2026-11-08）：先用關鍵字判讀，排程預設 `AI_PROVIDER=rules`；日後要換 AI 只需設定 GitHub Variables／Secrets，不需改程式 |
| 14 | 法說會日期沒有證交所／櫃買 OpenAPI，公開資訊觀測站需表單查詢 | 先由網站手動登錄（第 9 階段），events.source = manual |
| 15 | 財經新聞 RSS 來源（filters.yaml 的 rss_feeds）：需擁有者選擇條款允許自動讀取的來源；目前只用公開資訊觀測站重大訊息 | 待擁有者提供 |
| 16 | 濾網門檻（燈號各輸入的區間、外資未平倉 ±口數、弱勢族群比例 30%）為初始值 | 第 10 階段回測後調整 |
| 17 | 總資金目前使用資料庫預設 100 萬（settings.total_capital），影響建議張數 | 可用 `python -m engine.cli capital 金額` 或 GitHub Actions 手動操作 `capital` 設定；第 9 階段網站設定頁也可修改 |
| 18 | 進場條件、價位與風控參數（risk.yaml）為初始值 | 第 10 階段回測後調整 |
| 19 | LINE 官方帳號免費方案每月推播則數有上限，請在 LINE Official Account Manager 確認目前方案額度；每日報告通常 1 則、有警告時加 1 則 | 待擁有者確認；超過時可把 notify.yaml 的 max_messages_per_report 調小 |
| 20 | 付費模式回測需包含已下市股票，但目前 stocks 沒有下市資料（FinMind 有 TaiwanStockDelisting，尚未實測是否免費） | 升級付費方案前補上；報告會標註倖存者偏差 |
| 21 | 回測期間：預設從 2023-01 起、最後 12 個月為保留區間；評分需要 240 日股價＋3 年自身歷史，自選股請先以 `backfill --years 5` 回補 | 擁有者依資料長度調整 config/backtest.yaml |
| 22 | 首次在本機以 8 檔自選股、2024-11 至 2025-11 回測（調參區間）：年化 +14.8%、最大回撤 −8.3%、期望值 +0.99%，同期 0050 含息 +35.2%；出場以「分數跌出門檻」最多（14／35）。僅為測試資料，正式調參待擁有者決定 | 參考 |
