-- 消息面擴大（2026-11-12）：除了個股新聞，也收產業（族群）與盤勢新聞
-- scope：stock 個股／group 族群／market 盤勢；topic：族群名稱、關鍵字或新聞分類
ALTER TABLE news ADD COLUMN IF NOT EXISTS scope text NOT NULL DEFAULT 'stock';
ALTER TABLE news ADD COLUMN IF NOT EXISTS topic text;
CREATE INDEX IF NOT EXISTS news_scope_published_idx ON news (scope, published_at);
