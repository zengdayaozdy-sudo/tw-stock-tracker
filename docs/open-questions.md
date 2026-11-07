# 待確認事項

開發時查不到或需要擁有者決定的事項記在這裡。

| # | 問題 | 狀態 |
|---|---|---|
| 1 | 券商分點 `TaiwanStockTradingDailyReport`：官方文件標示單檔查詢需 Sponsor，全市場一次查詢需 Sponsor Pro | 已確認（2026-11-07 FinMind Chip 文件），plans.yaml 目前只放在 pro，若只需自選股可改放 sponsor |
| 2 | FinMind 即時報價屬於哪個方案 | 待確認 |
| 3 | FinMind 額度：有 token 每小時 600 次、無 token 300 次；付費方案上限待確認 | 免費額度已確認，sources.yaml 付費上限暫填 6000 |
| 4 | 股權分散表 `TaiwanStockHoldingSharesPer` 需付費；免費模式改用集保公開資料 | 已確認，集保端點待第 3 階段實作並實測 |
| 5 | 全市場不帶 data_id 的一次查詢（法人、融資券、外資持股）需 backer／sponsor | 已確認，免費模式改用證交所／櫃買公開資料 |
| 6 | 證交所／櫃買 OpenAPI 端點與欄位名稱 | 開發環境無法連線，需在 GitHub Actions 執行「手動操作 → probe」實測後，把 sources.yaml 的 verified 改成 true |
| 7 | 證交所公司資料的產業別是代碼，需對照成產業名稱 | probe 確認格式後補對照表 |
| 8 | 費城半導體、台積電 ADR、台指期未平倉的免費資料來源 | 第 4 階段確認 |
| 9 | 回測採用 FinLab 或開源套件 | 第 10 階段決定，寫入 decisions.md |
| 10 | 盤中監控常駐程式要跑在哪台電腦（作業系統） | 待擁有者回覆 |
