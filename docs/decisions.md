# 技術決策紀錄

| 日期 | 決策 | 理由 |
|---|---|---|
| 2026-11-07 | 方案切換以 `config/plans.yaml` 的 feature flags 實作，程式只檢查功能開關 | 升級 FinMind 方案只需改 PLAN，不需改程式 |
| 2026-11-07 | 資料庫 migration 用純 SQL 檔，依檔名順序套用並記錄在 schema_migrations | 簡單、可讀，Neon 直接相容 |
| 2026-11-07 | 新增 valuations 與 api_usage 兩張輔助表 | 本益比等每日估值與 FinMind 額度計數需要獨立儲存 |
