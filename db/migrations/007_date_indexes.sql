-- 依日期查詢的索引。
-- 主鍵都以 stock_id 開頭，「某一天全部股票」的查詢（完整性檢查筆數、網站最新日期、
-- 當日分數排行、市值更新）原本只能整表掃描，資料累積數年後會越來越慢。
CREATE INDEX IF NOT EXISTS daily_prices_date_idx ON daily_prices (date);
CREATE INDEX IF NOT EXISTS chips_date_idx ON chips (date);
CREATE INDEX IF NOT EXISTS scores_date_idx ON scores (date, horizon);
-- 個股頁的訊號紀錄（依代號、日期新到舊）
CREATE INDEX IF NOT EXISTS signals_stock_date_idx ON signals (stock_id, date DESC);
-- 每日報告與警告依執行日期查排程紀錄
CREATE INDEX IF NOT EXISTS job_logs_run_date_idx ON job_logs (run_date, job);
