-- 第 5 階段：訊號細節（評分快照、價位計算依據、風控張數計算過程、事件提示）
ALTER TABLE signals ADD COLUMN IF NOT EXISTS detail jsonb NOT NULL DEFAULT '{}';
ALTER TABLE signals ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS signals_date_idx ON signals (date, side);
