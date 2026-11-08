# 待確認事項

開發時查不到或需要擁有者決定的事項記在這裡。

| # | 問題 | 狀態 |
|---|---|---|
| 1 | 券商分點 `TaiwanStockTradingDailyReport`：官方文件標示單檔查詢需 Sponsor，全市場一次查詢需 Sponsor Pro | 已確認（2026-11-07 FinMind Chip 文件），plans.yaml 目前只放在 pro，若只需自選股可改放 sponsor |
| 2 | FinMind 即時報價屬於哪個方案 | 待確認 |
| 3 | FinMind 額度：有 token 每小時 600 次、無 token 300 次；付費方案上限待確認 | 免費額度已確認，sources.yaml 付費上限暫填 6000 |
| 4 | 股權分散表 `TaiwanStockHoldingSharesPer` 需付費；免費模式改用集保公開資料 | 已確認，集保端點待第 3 階段實作並實測 |
| 5 | 全市場不帶 data_id 的一次查詢（法人、融資券、外資持股）需 backer／sponsor | 已確認，免費模式改用證交所／櫃買公開資料 |
| 6 | 證交所／櫃買 OpenAPI 端點與欄位名稱 | 已確認：2026-11-06 實際抓取，上市 1,083 檔、上櫃 802 檔三大法人與融資券欄位皆正確 |
| 7 | 產業別是代碼 | 已處理：config/industries.yaml 對照表，查無代碼時顯示代碼 |
| 8 | 費城半導體、台積電 ADR、台指期未平倉的免費資料來源 | 已確認（2026-11-08 實測）：FinMind USStockPrice（^SOX、TSM）、TaiwanExchangeRate（USD）、TaiwanFuturesInstitutionalInvestors（TX），不帶 token 也可取得 |
| 9 | 回測採用 FinLab 或開源套件 | 第 10 階段決定，寫入 decisions.md |
| 10 | 盤中監控常駐程式要跑在哪台電腦（作業系統） | 待擁有者回覆 |
| 11 | FinMind 財報 type 名稱（EPS、GrossProfit、OperatingIncome、IncomeAfterTaxes、Equity、Liabilities、TotalAssets）需實測確認，名稱寫在 engine/scoring/indicators.py 的 METRIC_KEYS | 回補資料後檢查評分拆解是否還有「資料缺漏」 |
| 12 | 免費模式市值 | 已處理：公司資料實收資本額 ÷ 面額 = 股數，每日 × 收盤價更新市值 |
| 13 | 消息面 AI 判讀在 GitHub Actions 上連不到本機 Ollama。選項：(a) 設 `AI_PROVIDER=claude` 並加 `ANTHROPIC_API_KEY` Secret；(b) 在自己電腦用 Ollama 補跑 `python -m engine.cli news`（之後再跑 `filters` 更新排除名單）；(c) 維持關鍵字判讀 | 待擁有者決定；目前未設定時自動改用關鍵字判讀 |
| 14 | 法說會日期沒有證交所／櫃買 OpenAPI，公開資訊觀測站需表單查詢 | 先由網站手動登錄（第 9 階段），events.source = manual |
| 15 | 財經新聞 RSS 來源（filters.yaml 的 rss_feeds）：需擁有者選擇條款允許自動讀取的來源；目前只用公開資訊觀測站重大訊息 | 待擁有者提供 |
| 16 | 濾網門檻（燈號各輸入的區間、外資未平倉 ±口數、弱勢族群比例 30%）為初始值 | 第 10 階段回測後調整 |
