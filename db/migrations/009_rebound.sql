-- 大盤反彈偵測（2026-11-11）：每日反彈狀態、證據與寬度指標
ALTER TABLE market ADD COLUMN IF NOT EXISTS rebound jsonb;
